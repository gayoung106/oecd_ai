from src.oecd_aim.parser import parse_result_count, parse_cards

SAMPLE='''<html><body><div>Results: About 1 incidents & hazards</div><div class="card"><a href="/en/incidents/2026-09-20-5c05">Example incident</a><div>2026-09-20</div><div>8 articles</div><div>India</div><div>This is a long summary describing an AI-related incident and its consequences for an organisation and its users in sufficient detail.</div><div>AI principles:</div><div>Accountability</div><div>Industries:</div><div>IT infrastructure and hosting</div><div>Affected stakeholders:</div><div>Business</div><div>Harm types:</div><div>Economic/Property</div><div>Autonomy level:</div><div>High-action autonomy (human-out-of-the-loop)</div><div>AI system task:</div><div>Content generation</div><div>Why's our monitor labelling this an incident or hazard?</div><div>Therefore, it qualifies as an AI Incident.</div></div></body></html>'''

def test_count():
    assert parse_result_count(SAMPLE)==1

def test_card():
    r=parse_cards(SAMPLE,'https://oecd.ai')[0]
    assert r['incident_id']=='2026-09-20-5c05'
    assert r['country']=='India'
    assert r['incident_or_hazard']=='Incident'
