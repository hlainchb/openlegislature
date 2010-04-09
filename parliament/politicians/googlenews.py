import feedparser
import datetime
import hashlib

from django.utils.http import urlquote
from BeautifulSoup import BeautifulSoup
from django.utils.html import strip_tags, escape

from parliament.activity import utils as activity

GOOGLE_NEWS_URL = 'http://news.google.ca/news?pz=1&cf=all&ned=ca&hl=en&as_maxm=3&q=MP+%%22%s%%22+location%%3Acanada&as_qdr=a&as_drrb=q&as_mind=25&as_minm=2&cf=all&as_maxd=27&scoring=n&output=rss'
def get_feed(pol):
    return feedparser.parse(GOOGLE_NEWS_URL % urlquote(pol.name))
    
def news_items_for_pol(pol):
    feed = get_feed(pol)
    items = []
    for i in feed['entries'][:10]:
        item = {'url': i.link}
        title_elements = i.title.split('-')
        item['source'] = title_elements.pop().strip()
        item['title'] = '-'.join(title_elements).strip()
        item['date'] = datetime.date(*i.updated_parsed[:3])
        h = hashlib.md5()
        h.update(i.id)
        item['guid'] = h.hexdigest()
        soup = BeautifulSoup(i.summary)
        try:
            item['summary'] = strip_tags(str(soup.findAll('font', size='-1')[1]))
        except Exception, e:
            print e
            continue
        if pol.name not in item['summary']:
            continue
        items.append(item)
    return items
    
def save_politician_news(pol):
    items = news_items_for_pol(pol)
    for item in items:
        activity.save_activity(item, politician=pol, date=item['date'], guid=item['guid'], variety='gnews')