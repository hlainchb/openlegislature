{% load markup thumbnail %}
{% if page.has_previous or page.has_next %}<div class="focus">{% include "pagination.inc" %}</div>{% endif %}
{% for statement in page.object_list %}
<div class="statement nocontext{% if forloop.first %} first{% endif %}{% if highlight_statement == statement.sequence %} highlighted{% endif %}" id="s{{ statement.sequence }}" data-url="{{ statement.get_absolute_url }}">
<div class="focus nophoto"><div><p class="speaking"><a href="{{ statement.get_absolute_url }}#hl" class="statement_topic">{{ statement.topic }}</a> <span class="pol_affil">{{ statement.time|date:"F jS, Y" }}</span></p>
{{ statement.text_html }}</div></div>
<div class="clear"></div>
</div>
{% endfor %}
<div class="focus statement_browser">{% include "pagination.inc" %}</div>
