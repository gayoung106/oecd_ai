from src.oecd_aim.parser import parse_result_count, parse_cards, parse_detail_categories

SAMPLE='''<html><body><div>Results: About 1 incidents & hazards</div><div class="card"><a href="/en/incidents/2026-09-20-5c05">Example incident</a><div>2026-09-20</div><div>8 articles</div><div>India</div><div>This is a long summary describing an AI-related incident and its consequences for an organisation and its users in sufficient detail.</div><div>AI principles:</div><div>Accountability</div><div>Industries:</div><div>IT infrastructure and hosting</div><div>Affected stakeholders:</div><div>Business</div><div>Harm types:</div><div>Economic/Property</div><div>Autonomy level:</div><div>High-action autonomy (human-out-of-the-loop)</div><div>AI system task:</div><div>Content generation</div><div>Why's our monitor labelling this an incident or hazard?</div><div>Therefore, it qualifies as an AI Incident.</div></div></body></html>'''

def test_count():
    assert parse_result_count(SAMPLE)==1

def test_card():
    r=parse_cards(SAMPLE,'https://oecd.ai')[0]
    assert r['incident_id']=='2026-09-20-5c05'
    assert r['country']=='India'
    assert r['incident_or_hazard']=='Incident'

def test_card_ignores_show_more_tokens():
    html='''<html><body><div class="card"><a href="/en/incidents/2024-12-03-4768">Example incident</a><div>2024-12-03</div><div>7 articles</div><div>United States</div><div>This is a long summary describing an AI-related incident and the harms involved in enough detail.</div><div>AI principles:</div><div>Accountability</div><div>Human wellbeing</div><button><span>Show More (5)</span></button><div>Industries:</div><div>IT infrastructure and hosting</div><button><span>Show More (2)</span></button><div>Affected stakeholders:</div><div>Workers</div><div>General public</div><div>Harm types:</div><div>Economic/Property</div><div>Psychological</div><button><span>Show More (3)</span></button><div>Business function:</div><div>Research and development</div><button><span>Show More (2)</span></button><div>AI system task:</div><div>Content generation</div><button><span>Show More (2)</span></button></div></body></html>'''
    r=parse_cards(html,'https://oecd.ai')[0]

    checked_fields = [
        'ai_principles',
        'industries',
        'affected_stakeholders',
        'harm_types',
        'business_function',
        'ai_system_task',
    ]
    assert all('Show More' not in (r[field] or '') for field in checked_fields)
    assert r['ai_principles']=='Accountability | Human wellbeing'
    assert r['_collapsed_category_fields']==[
        'ai_principles',
        'industries',
        'harm_types',
        'business_function',
        'ai_system_task',
    ]

def test_detail_categories_extract_full_values():
    html='''<html><body><h1>Example incident</h1><h3>Why's our monitor labelling this an incident or hazard?</h3><p>Reason text.</p><h3>AI principles</h3><span>Accountability</span><span>Human wellbeing</span><span>Respect of human rights</span><h3>Industries</h3><span>IT infrastructure and hosting</span><span>Business processes and support services</span><span>IT infrastructure and hosting</span><h3>Affected stakeholders</h3><span>Workers</span><span>General public</span><h3>Harm types</h3><span>Economic/Property</span><span>Psychological</span><h3>Business function:</h3><span>Research and development</span><span>Other</span><h3>AI system task:</h3><span>Content generation</span><span>Interaction support/chatbots</span><h2>Articles about this incident or hazard</h2><p>Article title</p></body></html>'''
    r=parse_detail_categories(html)

    assert r['ai_principles']=='Accountability | Human wellbeing | Respect of human rights'
    assert r['industries']=='IT infrastructure and hosting | Business processes and support services'
    assert r['business_function']=='Research and development | Other'
    assert r['ai_system_task']=='Content generation | Interaction support/chatbots'

def test_card_with_only_classification_label_does_not_absorb_neighbor():
    html='''<html><body><div class="list"><div class="card"><a href="/en/incidents/2025-12-08-ce8c">First incident</a><div>2025-12-08</div><div>435 articles</div><div>United States</div><div>This summary is long enough to be selected as the incident summary without borrowing text from another card.</div><div>Why's our monitor labelling this an incident or hazard?</div><div>It qualifies as an AI Hazard.</div></div><div class="card"><a href="/en/incidents/2025-12-09-abcd">Second incident</a><div>2025-12-09</div><div>2 articles</div><div>Canada</div><div>This is the neighboring incident summary and must never appear in the first incident.</div><div>AI principles:</div><div>Accountability</div><button>Show More (2)</button><div>Why's our monitor labelling this an incident or hazard?</div><div>It qualifies as an AI Incident.</div></div></div></body></html>'''
    rows=parse_cards(html,'https://oecd.ai')
    first=next(row for row in rows if row['incident_id']=='2025-12-08-ce8c')

    assert first['date']=='2025-12-08'
    assert first['ai_principles'] is None
    assert first['_collapsed_category_fields']==[]
