import datetime, re

from django import template

from parliament.core.models import PROVINCE_LOOKUP

register = template.Library()

@register.filter(name='expand_province')
def expand_province(value):
    return PROVINCE_LOOKUP[value]
    
@register.filter(name='heshe')
def heshe(pol):
    if pol.gender == 'F':
        return 'She'
    elif pol.gender =='M':
        return 'He'
    else:
        return 'They'
        
@register.filter(name='hisher')
def heshe(pol):
    if pol.gender == 'F':
        return 'Her'
    elif pol.gender == 'M':
        return 'His'
    else:
        return 'Their'
        
@register.filter(name='month_num')
def month_num(month):
    return datetime.date(2010, month, 1).strftime("%B")
    
@register.filter(name='strip_act')
def strip_act(value):
    return re.sub(r'An Act (to )?([a-z])', lambda m: m.group(2).upper(), value)
    
@register.filter(name='time_since')
def time_since(value):
    today = datetime.date.today()
    days_since = (today - value).days
    if value > today or days_since == 0:
        return 'Today'
    elif days_since == 1:
        return 'Yesterday'
    elif days_since == 2:
        return 'Two days ago'
    elif days_since == 3:
        return 'Three days ago'
    elif days_since < 7:
        return 'This week'
    elif days_since < 14:
        return 'A week ago'
    elif days_since < 21:
        return 'Two weeks ago'
    elif days_since < 28:
        return 'Three weeks ago'
    elif days_since < 45:
        return 'A month ago'
    elif days_since < 75:
        return 'Two months ago'
    elif days_since < 105:
        return 'Three months ago'
    else:
        return 'More than three months ago'