# coding: utf-8
import urllib, urllib2, re, os.path, gzip
import datetime
from decimal import Decimal


from django.db import models, backend
from django.contrib import databrowse
from django.conf import settings
from BeautifulSoup import BeautifulSoup

from parliament.core import parsetools, text_utils
from parliament.core.utils import memoize_property, ActiveManager

POL_LOOKUP_URL = 'http://webinfo.parl.gc.ca/MembersOfParliament/ProfileMP.aspx?Key=%d&Language=E'

class InternalXref(models.Model):
    text_value = models.CharField(max_length=50, blank=True, db_index=True)
    int_value = models.IntegerField(blank=True, null=True, db_index=True)
    target_id = models.IntegerField(db_index=True)
    
    # CURRENT SCHEMAS
    # party_names
    # bill_callbackid
    # session_legisin -- LEGISinfo ID for a session
    # edid_postcode -- the EDID -- which points to a riding, but is NOT the primary  key -- for a postcode
    schema = models.CharField(max_length=15, db_index=True)
    
    def __unicode__(self):
        return u"%s: %s %s for %s" % (self.schema, self.text_value, self.int_value, self.target_id)

class PartyManager(models.Manager):
    
    def get_by_name(self, name):
        x = InternalXref.objects.filter(schema='party_names', text_value=name.strip().lower())
        if len(x) == 0:
            raise Party.DoesNotExist()
        elif len(x) > 1:
            raise Exception("More than one party matched %s" % name.strip().lower())
        else:
            return self.get_query_set().get(pk=x[0].target_id)
            
class Party(models.Model):
    name = models.CharField(max_length=100)
    slug = models.CharField(max_length=10, blank=True)
    short_name = models.CharField(max_length=100, blank=True)
    
    objects = PartyManager()
    
    class Meta:
        verbose_name_plural = 'Parties'
        ordering = ('name',)
        
    def __init__(self, *args, **kwargs):
        """ If we're creating a new object, set a flag to save the name to the alternate-names table. """
        super(Party, self).__init__(*args, **kwargs)
        self._saveAlternate = True

    def save(self):
        if not getattr(self, 'short_name', None):
            self.short_name = self.name
        super(Party, self).save()
        if getattr(self, '_saveAlternate', False):
            self.add_alternate_name(self.name)

    def delete(self):
        InternalXref.objects.filter(schema='party_names', target_id=self.id).delete()
        super(Party, self).delete()

    def add_alternate_name(self, name):
        name = name.strip().lower()
        # check if exists
        x = InternalXref.objects.filter(schema='party_names', text_value=name)
        if len(x) == 0:
            InternalXref(schema='party_names', target_id=self.id, text_value=name).save()
        else:
            if x[0].target_id != self.id:
                raise Exception("Name %s already points to a different party" % name.strip().lower())
                
    def __unicode__(self):
        return self.name

class Person(models.Model):
    
    name = models.CharField(max_length=100)
    name_given = models.CharField("Given name", max_length=50, blank=True)
    name_family = models.CharField("Family name", max_length=50, blank=True)

    def __unicode__(self):
        return self.name
    
    class Meta:
        abstract = True
        ordering = ('name',)

class PoliticianManager(models.Manager):
    
    def elected(self):
        return self.get_query_set().annotate(electedcount=models.Count('electedmember')).filter(electedcount__gte=1)
        
    def current(self):
        return self.get_query_set().filter(electedmember__end_date__isnull=True, electedmember__start_date__isnull=False).distinct()
        
    def elected_but_not_current(self):
        return self.get_query_set().exclude(electedmember__end_date__isnull=True)
    
    def filter_by_name(self, name):
        return [i.politician for i in 
            PoliticianInfo.sr_objects.filter(schema='alternate_name', value=parsetools.normalizeName(name))]
    filterByName = filter_by_name
    
    def get_by_name(self, name, session=None, riding=None, election=None, party=None, saveAlternate=True, strictMatch=False):
        """ Return a Politician by name. Uses a bunch of methods to try and deal with variations in names.
        If given any of a session, riding, election, or party, returns only politicians who match.
        If given session and optinally riding, tries to match the name more laxly.
        
        saveAlternate: If we have Thomas Mulcair and we match, via session/riding, to Tom Mulcair, save Tom
            in the alternate names table
        strictMatch: Even if given a session, don't try last-name-only matching.
        
        """
        
        # Alternate names for a pol are in the InternalXref table. Assemble a list of possibilities
        poss = PoliticianInfo.sr_objects.filter(schema='alternate_name', value=parsetools.normalizeName(name))
        if len(poss) >= 1:
            # We have one or more results
            if session or riding or party:
                # We've been given extra criteria -- see if they match
                result = None
                for p in poss:
                    # For each possibility, assemble a list of matching Members
                    members = ElectedMember.objects.filter(politician=p.politician)
                    if riding: members = members.filter(riding=riding)
                    if session: members = members.filter(sessions=session)
                    if party: members = members.filter(party=party)
                    if len(members) >= 1:
                        if result: # we found another match on a previous journey through the loop
                            # can't disambiguate, raise exception
                            raise Politician.MultipleObjectsReturned(name)
                        # We match! Save the result.
                        result = members[0].politician
                if result:
                    return result
            elif election:
                raise Exception("Election not implemented yet in Politician get_by_name")
            else:
                # No extra criteria -- return what we got (or die if we can't disambiguate)
                if len(poss) > 1:
                    raise Politician.MultipleObjectsReturned(name)
                else:
                    return poss[0].politician
        if session and not strictMatch:
            # We couldn't find the pol, but we have the session and riding, so let's give this one more shot
            # We'll try matching only on last name
            match = re.search(r'\s([A-Z][\w-]+)$', name.strip()) # very naive lastname matching
            if match:
                lastname = match.group(1)
                pols = self.get_query_set().filter(name_family=lastname, electedmember__sessions=session).distinct()
                if riding:
                    pols = pols.filter(electedmember__riding=riding)
                if len(pols) > 1:
                    if riding:
                        raise Exception("DATA ERROR: There appear to be two politicians with the same last name elected to the same riding from the same session... %s %s %s" % (lastname, session, riding))
                elif len(pols) == 1:
                    # yes!
                    pol = pols[0]
                    if saveAlternate:
                        pol.add_alternate_name(name) # save the name we were given as an alternate
                    return pol
        raise Politician.DoesNotExist("Could not find politician named %s" % name)
        
    def get_by_parlinfo_id(self, parlinfoid, session=None):
        PARLINFO_LOOKUP_URL = 'http://www2.parl.gc.ca/parlinfo/Files/Parliamentarian.aspx?Item=%s&Language=E'
        try:
            info = PoliticianInfo.sr_objects.get(schema='parlinfo_id', value=parlinfoid.lower())
            return info.politician
        except PoliticianInfo.DoesNotExist:
            print "Looking up parlinfo ID %s" % parlinfoid 
            parlinfourl = PARLINFO_LOOKUP_URL % parlinfoid
            parlinfopage = urllib2.urlopen(parlinfourl).read()
            match = re.search(
              r'href="http://webinfo\.parl\.gc\.ca/MembersOfParliament/ProfileMP\.aspx\?Key=(\d+)&amp;Language=E">MP profile',
              parlinfopage)
            if not match:
                raise Politician.DoesNotExist
            pol = self.get_by_parl_id(match.group(1), session=session)
            pol.save_parlinfo_id(parlinfoid)
            return pol
    
    def get_by_parl_id(self, parlid, session=None, election=None, lookOnline=True):
        try:
            info = PoliticianInfo.sr_objects.get(schema='parl_id', value=unicode(parlid))
            return info.politician
            # FIXME label-as-invalid functionality
            #if polid < 0:
            #    raise Politician.DoesNotExist("Stored as invalid parlID")
        except PoliticianInfo.DoesNotExist:
            if not lookOnline:
                return None # FIXME inconsistent behaviour: when should we return None vs. exception?
            print "Unknown parlid %d... " % parlid,
            soup = BeautifulSoup(urllib2.urlopen(POL_LOOKUP_URL % parlid))
            if soup.find('table', id='MasterPage_BodyContent_PageContent_PageContent_pnlError'):
                print "Error page for parlid %d" % parlid
                raise Politician.DoesNotExist("Invalid page for parlid %s" % parlid)
            polname = soup.find('span', id='MasterPage_MasterPage_BodyContent_PageContent_Content_TombstoneContent_TombstoneContent_ucHeaderMP_lblMPNameData').string
            polriding = soup.find('a', id='MasterPage_MasterPage_BodyContent_PageContent_Content_TombstoneContent_TombstoneContent_ucHeaderMP_hlConstituencyProfile').string
            parlinfolink = soup.find('a', id='MasterPage_MasterPage_BodyContent_PageContent_Content_TombstoneContent_TombstoneContent_ucHeaderMP_hlFederalExperience')
                        
            try:
                riding = Riding.objects.get_by_name(polriding)
            except Riding.DoesNotExist:
                raise Politician.DoesNotExist("Couldn't find riding %s" % polriding)
            if session:
                pol = self.get_by_name(name=polname, session=session, riding=riding)
            else:
                pol = self.get_by_name(name=polname, riding=riding)
            print "found %s." % pol
            pol.save_parl_id(parlid)
            polid = pol.id
            if parlinfolink:
                match = re.search(r'Item=(.+?)&', parlinfolink['href'])
                pol.save_parlinfo_id(match.group(1))
            return self.get_query_set().get(pk=polid)
    getByParlID = get_by_parl_id

class Politician(Person):
    GENDER_CHOICES = (
        ('M', 'Male'),
        ('F', 'Female'),
    )

    dob = models.DateField(blank=True, null=True)
    site = models.URLField(blank=True, verify_exists=False)
    parlpage = models.URLField(blank=True, verify_exists=False)
    gender = models.CharField(max_length=1, blank=True, choices=GENDER_CHOICES)
    headshot = models.ImageField(upload_to='polpics', blank=True, null=True)
    slug = models.CharField(max_length=30, blank=True, db_index=True)
    
    objects = PoliticianManager()
    
    def __init__(self, *args, **kwargs):
        """ If we're creating a new object, set a flag to save the name to the alternate-names table. """
        super(Politician, self).__init__(*args, **kwargs)
        self._saveAlternate = True
        
    def add_alternate_name(self, name):
        self.set_info_multivalued('alternate_name', parsetools.normalizeName(name))

    def alternate_names(self):
        return self.politicianinfo_set.filter(schema='alternate_name').values_list('value', flat=True)
        
    @property
    @memoize_property
    def current_member(self):
        try:
            return ElectedMember.objects.get(politician=self, end_date__isnull=True)
        except ElectedMember.DoesNotExist:
            return False

    @property
    @memoize_property        
    def latest_member(self):
        try:
            return ElectedMember.objects.filter(politician=self).order_by('-start_date').select_related('party', 'riding')[0]
        except IndexError:
            return None

    @property
    @memoize_property
    def latest_candidate(self):
        try:
            return self.candidacy_set.order_by('-election__date').select_related('election')[0]
        except IndexError:
            return None
        
    def delete(self):
        super(Politician, self).delete()
        
    def save(self):
        super(Politician, self).save()
        if getattr(self, '_saveAlternate', False):
            self.add_alternate_name(self.name)

    def save_parl_id(self, parlid):
        if PoliticianInfo.objects.filter(schema='parl_id', value=unicode(parlid)).exists():
            raise Exception("ParlID %d already in use" % parlid)
        self.set_info_multivalued('parl_id', parlid)
        
    def save_parlinfo_id(self, parlinfoid):
        self.set_info('parlinfo_id', parlinfoid.lower())
            
    @models.permalink
    def get_absolute_url(self):
        if self.slug:
            return ('parliament.politicians.views.politician', [], {'pol_slug': self.slug})
        return ('parliament.politicians.views.politician', [], {'pol_id': self.id})
        
    @models.permalink
    def get_contact_url(self):
        if self.slug:
            return ('parliament.contact.views.contact_politician', [], {'pol_slug': self.slug})
        return ('parliament.contact.views.contact_politician', [], {'pol_id': self.id})
            
    @memoize_property
    def info(self):
        return dict([i for i in self.politicianinfo_set.all().values_list('schema', 'value')])
        
    @memoize_property
    def info_multivalued(self):
        info = {}
        for i in self.politicianinfo_set.all().values_list('schema', 'value'):
            info.setdefault(i[0], []).append(i[1])
        return info
        
    def set_info(self, key, value):
        try:
            info = self.politicianinfo_set.get(schema=key)
        except PoliticianInfo.DoesNotExist:
            info = PoliticianInfo(politician=self, schema=key)
        info.value = unicode(value)
        info.save()
        
    def set_info_multivalued(self, key, value):
        PoliticianInfo.objects.get_or_create(politician=self, schema=key, value=unicode(value))
        
    def find_favourite_word(self):
        self.set_info('favourite_word', text_utils.most_frequent_word(self.statement_set.all()))
        
class PoliticianInfoManager(models.Manager):
    """Custom manager ensures we always pull in the politician FK."""
    
    def get_query_set(self):
        return super(PoliticianInfoManager, self).get_query_set()\
            .select_related('politician')

# Not necessarily a full list           
POLITICIAN_INFO_SCHEMAS = (
    'alternate_name',
    'twitter',
    'parl_id',
    'parlinfo_id',
    'freebase_id',
    'wikipedia_id'
)
            
class PoliticianInfo(models.Model):
    politician = models.ForeignKey(Politician)
    schema = models.CharField(max_length=40, db_index=True)
    value = models.CharField(max_length=500)
    
    objects = models.Manager()
    sr_objects = PoliticianInfoManager()

    def __unicode__(self):
        return u"%s: %s" % (self.politician, self.schema)
        
    @property
    def int_value(self):
        return int(self.value)

class SessionManager(models.Manager):
    
    def with_bills(self):
        return self.get_query_set().filter(bill__number_only__isnull=False)
    
    def current(self):
        return self.get_query_set().order_by('-start')[0]
        
class Session(models.Model):
    
    id = models.CharField(max_length=4, primary_key=True)
    name = models.CharField(max_length=100)
    start = models.DateField()
    end = models.DateField(blank=True, null=True)
    parliamentnum = models.IntegerField(blank=True, null=True)
    sessnum = models.IntegerField(blank=True, null=True)

    objects = SessionManager()
    
    class Meta:
        ordering = ('-start',)

    def __unicode__(self):
        return self.name
        
    def has_votes(self):
        return bool(self.votequestion_set.all().count())
    
class RidingManager(models.Manager):
    
    # FIXME: This should really be in the database, not the model
    FIX_RIDING = {
        'richmond-arthabasca' : 'richmond-arthabaska',
        'richemond-arthabaska' : 'richmond-arthabaska',
        'battle-river' : 'westlock-st-paul',
        'vancouver-est': 'vancouver-east',
        'calgary-ouest': 'calgary-west',
        'kitchener-wilmot-wellesley-woolwich': 'kitchener-conestoga',
        'carleton-orleans': 'ottawa-orleans',
        'frazer-valley-west': 'fraser-valley-west',
        'laval-ouest': 'laval-west',
        'medecine-hat': 'medicine-hat',
        'lac-st-jean': 'lac-saint-jean',
        'vancouver-north': 'north-vancouver',
        'laval-est': 'laval-east',
        'ottawa-ouest-nepean': 'ottawa-west-nepean',
        'cap-breton-highlands-canso': 'cape-breton-highlands-canso',
        'winnipeg-centre-sud': 'winnipeg-south-centre',
        'renfrew-nippissing-pembroke': 'renfrew-nipissing-pembroke',
        'the-battleford-meadow-lake': 'the-battlefords-meadow-lake',
        'esquimalt-de-fuca': 'esquimalt-juan-de-fuca',
        'sint-hubert': 'saint-hubert',
        #'edmonton-mill-woods-beaumont': 'edmonton-beaumont',
    }
    
    def get_by_name(self, name):
        slug = parsetools.slugify(name)
        if slug in RidingManager.FIX_RIDING:
            slug = RidingManager.FIX_RIDING[slug]
        return self.get_query_set().get(slug=slug)
    get_by_name = get_by_name

PROVINCE_CHOICES = (
    ('AB', 'Alberta'),
    ('BC', 'B.C.'),
    ('SK', 'Saskatchewan'),
    ('MB', 'Manitoba'),
    ('ON', 'Ontario'),
    ('QC', 'Québec'),
    ('NB', 'New Brunswick'),
    ('NS', 'Nova Scotia'),
    ('PE', 'P.E.I.'),
    ('NL', 'Newfoundland & Labrador'),
    ('YT', 'Yukon'),
    ('NT', 'Northwest Territories'),
    ('NU', 'Nunavut'),
)
PROVINCE_LOOKUP = dict(PROVINCE_CHOICES)
class Riding(models.Model):
    name = models.CharField(max_length=60)
    province = models.CharField(max_length=2, choices=PROVINCE_CHOICES)
    slug = models.CharField(max_length=60, unique=True, db_index=True)
    edid = models.IntegerField(blank=True, null=True, db_index=True)
    
    objects = RidingManager()
    
    class Meta:
        ordering = ('province', 'name')
        
    def save(self):
        if not self.slug:
            self.slug = parsetools.slugify(self.name)
        super(Riding, self).save()
        
    @property
    def dashed_name(self):
        return self.name.replace('--', u'—')
        
    def __unicode__(self):
        return "%s (%s)" % (self.dashed_name, self.get_province_display())
        
class ElectedMemberManager(models.Manager):
    
    def current(self):
        return self.get_query_set().filter(end_date__isnull=True)
        
    def former(self):
        return self.get_query_set().filter(end_date__isnull=False)
    
    def on_date(self, date):
        return self.get_query_set().filter(models.Q(start_date__lte=date)
            & (models.Q(end_date__isnull=True) | models.Q(end_date__gte=date)))
    
    def get_by_pol(self, politician, date=None, session=None):
        if not date and not session:
            raise Exception("Provide either a date or a session to get_by_pol.")
        if date:
            return self.on_date(date).get(politician=politician)
        else:
            # In the case of floor crossers, there may be more than one ElectedMember
            # We haven't been given a date, so just return the first EM
            qs = self.get_query_set().filter(politician=politician, sessions=session).order_by('-start_date')
            if not len(qs):
                raise ElectedMember.DoesNotExist("No elected member for %s, session %s" % (politician, session))
            return qs[0]
    
class ElectedMember(models.Model):
    sessions = models.ManyToManyField(Session)
    politician = models.ForeignKey(Politician)
    riding = models.ForeignKey(Riding)
    party = models.ForeignKey(Party)
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(blank=True, null=True, db_index=True)
    
    objects = ElectedMemberManager()
    
    def __unicode__ (self):
        if self.end_date:
            return u"%s (%s) was the member from %s from %s to %s" % (self.politician, self.party, self.riding, self.start_date, self.end_date)
        else:
            return u"%s (%s) is the member from %s (since %s)" % (self.politician, self.party, self.riding, self.start_date)
            
    @property
    def current(self):
        return not bool(self.end_date)
        
class SiteNews(models.Model):
    date = models.DateTimeField(default=datetime.datetime.now)
    title = models.CharField(max_length=200)
    text = models.TextField()
    active = models.BooleanField(default=True)
    
    objects = models.Manager()
    public = ActiveManager()
    
    class Meta:
        ordering = ('-date',)

