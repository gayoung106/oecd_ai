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
SHOW_MORE_LESS_RE = re.compile(r'^show\s+(?:more|less)(?:\s*\(\d+\))?$', re.I)
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
CATEGORY_FIELDS = {
    'ai_principles',
    'industries',
    'affected_stakeholders',
    'harm_types',
    'business_function',
    'ai_system_task',
}
DETAIL_FIELD_BY_LABEL = {
    'AI principles':'ai_principles',
    'AI principles:':'ai_principles',
    'Industries':'industries',
    'Industries:':'industries',
    'Affected stakeholders':'affected_stakeholders',
    'Affected stakeholders:':'affected_stakeholders',
    'Harm types':'harm_types',
    'Harm types:':'harm_types',
    'Business function':'business_function',
    'Business function:':'business_function',
    'AI system task':'ai_system_task',
    'AI system task:':'ai_system_task',
}
DETAIL_STOP_LABELS = {
    'Articles about this incident or hazard',
    "Why's our monitor labelling this an incident or hazard?",
}

def normalize_space(s):
    return re.sub(r'\s+', ' ', s or '').strip()

def _is_ui_token(s):
    return bool(SHOW_MORE_LESS_RE.fullmatch(normalize_space(s)))

def _unique_values(values):
    return list(dict.fromkeys(values))

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
        if hits >= 1 and DATE_RE.search(text):
            best = node
            slugs = set()
            for x in node.find_all('a', href=True):
                m = INCIDENT_HREF_RE.match(x['href'].strip())
                if m:
                    slugs.add(m.group(1))
            if len(slugs) == 1:
                return node
    return best

def _line_tokens(card, keep_ui_tokens=False):
    lines = []
    for s in card.stripped_strings:
        t = normalize_space(str(s))
        if _is_ui_token(t) and not keep_ui_tokens:
            continue
        if t and (not lines or t != lines[-1]):
            lines.append(t)
    return lines

def _extract_labeled(lines, field_by_label=FIELD_BY_LABEL, stop_labels=None):
    out = {v:None for v in field_by_label.values()}
    labels = set(field_by_label)
    positions = [(i,t) for i,t in enumerate(lines) if t in field_by_label]
    for j,(idx,lab) in enumerate(positions):
        end = positions[j+1][0] if j+1 < len(positions) else len(lines)
        if stop_labels:
            stop_positions = [i for i in range(idx+1, end) if lines[i] in stop_labels]
            if stop_positions:
                end = min(stop_positions)
        vals = [x for x in lines[idx+1:end] if x not in labels and not _is_ui_token(x) and x != '* * *']
        if field_by_label[lab] in CATEGORY_FIELDS:
            vals = _unique_values(vals)
        sep = ' ' if lab.startswith("Why's") else ' | '
        out[field_by_label[lab]] = normalize_space(sep.join(vals)) or None
    return out

def _collapsed_category_fields(raw_lines):
    collapsed = []
    positions = [(i,t) for i,t in enumerate(raw_lines) if t in FIELD_BY_LABEL]
    for j,(idx,lab) in enumerate(positions):
        field = FIELD_BY_LABEL[lab]
        if field not in CATEGORY_FIELDS:
            continue
        end = positions[j+1][0] if j+1 < len(positions) else len(raw_lines)
        if any(_is_ui_token(x) for x in raw_lines[idx+1:end]):
            collapsed.append(field)
    return collapsed

def parse_detail_categories(html):
    soup = BeautifulSoup(html, 'lxml')
    lines = _line_tokens(soup)
    fields = _extract_labeled(lines, DETAIL_FIELD_BY_LABEL, DETAIL_STOP_LABELS)
    return {k:v for k,v in fields.items() if v}

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
        raw_lines = _line_tokens(card, keep_ui_tokens=True)
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
        collapsed_fields = _collapsed_category_fields(raw_lines)
        rows.append({
            'incident_id':slug, 'url':url, 'title':title, 'date':date,
            'country':country, 'summary':summary, 'article_count':article_count,
            **fields, 'incident_or_hazard':_infer_classification(fields.get('classification_reason')),
            '_collapsed_category_fields':collapsed_fields,
        })
    return rows
