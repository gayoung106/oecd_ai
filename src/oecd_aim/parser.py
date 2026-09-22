from __future__ import annotations
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag
import pycountry

COUNTRY_NAMES = {c.name.lower(): c.name for c in pycountry.countries}

COUNTRY_ALIASES = {
    'united states': 'United States',
    'usa': 'United States',
    'u.s.': 'United States',
    'united kingdom': 'United Kingdom',
    'uk': 'United Kingdom',
    'south korea': 'South Korea',
    'republic of korea': 'South Korea',
    'north korea': 'North Korea',
    'russia': 'Russia',
    'viet nam': 'Viet Nam',
    'turkey': 'Türkiye',
    'türkiye': 'Türkiye',
    'iran': 'Iran',
    'taiwan': 'Taiwan',
}

INCIDENT_HREF_RE = re.compile(r'^/en/incidents/(\d{4}-\d{2}-\d{2}-[A-Za-z0-9_-]+)(?:[/?#].*)?$')
DATE_RE = re.compile(r'\b(?:19|20)\d{2}-\d{2}-\d{2}\b')
ARTICLE_RE = re.compile(r'\b(\d[\d,]*)\s+articles?\b', re.I)
RESULT_COUNT_RE = re.compile(r'Results:\s*(?:About\s*)?([\d,]+)\s+incidents?\s*&\s*hazards?', re.I)
LABELS = [
    'AI principles:', 'Industries:', 'Affected stakeholders:', 'Harm types:',
    'Business function:', 'Autonomy level:', 'AI system task:',
    "Why's our monitor labelling this an incident or hazard?",
]
FIELD_BY_LABEL = {
    'AI principles:':'ai_principles', 'Industries:':'industries',
    'Affected stakeholders:':'affected_stakeholders', 'Harm types:':'harm_types',
    'Business function:':'business_function', 'Autonomy level:':'autonomy_level',
    'AI system task:':'ai_system_task',
    "Why's our monitor labelling this an incident or hazard?":'classification_reason',
}

def normalize_space(s):
    return re.sub(r'\s+', ' ', s or '').strip()

def parse_result_count(html):
    text = BeautifulSoup(html, 'lxml').get_text(' ', strip=True)
    m = RESULT_COUNT_RE.search(text)
    return int(m.group(1).replace(',','')) if m else None

def incident_links(html, base_url):
    soup = BeautifulSoup(html, 'lxml')
    out, seen = [], set()
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        m = INCIDENT_HREF_RE.match(href)
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        title = normalize_space(a.get_text(' ', strip=True))
        if not title:
            continue
        seen.add(slug)
        out.append((slug, urljoin(base_url, href), a))
    return out

def _find_card(anchor: Tag):
    node = anchor
    best = anchor.parent
    for _ in range(12):
        node = node.parent
        if not isinstance(node, Tag):
            break
        text = node.get_text('\n', strip=True)
        hits = sum(1 for lab in LABELS if lab in text)
        if hits >= 2 and DATE_RE.search(text):
            best = node
            slugs = set()
            for x in node.find_all('a', href=True):
                m = INCIDENT_HREF_RE.match(x['href'].strip())
                if m:
                    slugs.add(m.group(1))
            if len(slugs) == 1:
                return node
    return best

def _line_tokens(card):
    lines = []
    for s in card.stripped_strings:
        t = normalize_space(str(s))
        if t and (not lines or t != lines[-1]):
            lines.append(t)
    return lines

def _extract_labeled(lines):
    out = {v:None for v in FIELD_BY_LABEL.values()}
    positions = [(i,t) for i,t in enumerate(lines) if t in FIELD_BY_LABEL]
    for j,(idx,lab) in enumerate(positions):
        end = positions[j+1][0] if j+1 < len(positions) else len(lines)
        vals = [x for x in lines[idx+1:end] if x not in LABELS]
        sep = ' ' if lab.startswith("Why's") else ' | '
        out[FIELD_BY_LABEL[lab]] = normalize_space(sep.join(vals)) or None
    return out

def _infer_classification(reason):
    if not reason:
        return None
    tail = reason.lower()[-700:]
    if 'ai hazard' in tail or 'classified as a hazard' in tail or 'qualifies as an ai hazard' in tail:
        return 'Hazard'
    if 'ai incident' in tail or 'classified as an incident' in tail or 'qualifies as an ai incident' in tail:
        return 'Incident'
    r = reason.lower()
    if 'ai hazard' in r:
        return 'Hazard'
    if 'ai incident' in r:
        return 'Incident'
    return None

def parse_cards(html, base_url):
    rows = []
    for slug,url,a in incident_links(html, base_url):
        card = _find_card(a)
        lines = _line_tokens(card)
        text = ' '.join(lines)
        title = normalize_space(a.get_text(' ', strip=True))
        dm = DATE_RE.search(text)
        date = dm.group(0) if dm else None
        am = ARTICLE_RE.search(text)
        article_count = int(am.group(1).replace(',','')) if am else None
        first_label_idx = min([lines.index(l) for l in LABELS if l in lines], default=len(lines))
        prefix = lines[:first_label_idx]
        candidates = [x for x in prefix if x != title and not DATE_RE.fullmatch(x) and not ARTICLE_RE.fullmatch(x) and not x.startswith('Open in') and not x.lower().startswith('image')]
        summary = max(candidates, key=len) if candidates else None
        NAVIGATION_TOKENS = {
    'home',
    'aim',
    'ai incidents and hazards monitor',
    'open in new window',
    'ai generated',
}

        country = None

        # 국가명은 사건 카드의 title/date/article_count와 summary 사이에
        # 위치하는 짧은 토큰에서만 찾는다.
        summary_idx = None

        if summary in prefix:
            summary_idx = prefix.index(summary)

        if summary_idx is not None:
            country_candidates = prefix[:summary_idx]
        else:
            country_candidates = prefix

        # 뒤에서부터 탐색해야 title/date 직후의 실제 국가명이 우선됨
        for token in reversed(country_candidates):

            token_clean = token.strip()
            token_lower = token_clean.lower()

            if token_clean == title:
                continue

            if DATE_RE.fullmatch(token_clean):
                continue

            if ARTICLE_RE.fullmatch(token_clean):
                continue

            if token_lower in NAVIGATION_TOKENS:
                continue

            if token_lower in COUNTRY_ALIASES:
                country = COUNTRY_ALIASES[token_lower]
                break

            if token_lower in COUNTRY_NAMES:
                country = COUNTRY_NAMES[token_lower]
                break
        fields = _extract_labeled(lines)
        rows.append({
            'incident_id':slug, 'url':url, 'title':title, 'date':date,
            'country':country, 'summary':summary, 'article_count':article_count,
            **fields, 'incident_or_hazard':_infer_classification(fields.get('classification_reason')),
        })
    return rows
