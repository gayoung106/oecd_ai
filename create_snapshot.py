from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.oecd_aim.config import load_config


CATEGORY_COLUMNS = [
    'ai_principles',
    'industries',
    'harm_types',
    'business_function',
    'ai_system_task',
    'affected_stakeholders',
]
REQUIRED_COLUMNS = ['incident_id', 'url', 'title', 'date']
UI_TOKEN_RE = re.compile(r'\bShow\s+(?:More|Less)(?:\s*\(\d+\))?\b', re.I)
INCIDENT_LINK_RE = re.compile(r'/en/incidents/(\d{4}-\d{2}-\d{2}-[A-Za-z0-9_-]+)')


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for column in out.columns:
        out[column] = out[column].astype('string').fillna('')
    return out.reset_index(drop=True)


def nonempty(series: pd.Series) -> pd.Series:
    return series.astype('string').fillna('').str.strip().ne('')


def latest_range_records(path: Path) -> dict[tuple[str, str], dict]:
    records = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get('start') and item.get('end'):
            records[(item['start'], item['end'])] = item
    return records


def split_range(start: date, end: date) -> tuple[tuple[date, date], tuple[date, date]]:
    middle = start + timedelta(days=(end - start).days // 2)
    return (start, middle), (middle + timedelta(days=1), end)


def source_range_audit(root: Path, cfg: dict) -> tuple[pd.DataFrame, dict]:
    log_path = root / 'data' / 'logs' / 'range_log.jsonl'
    records = latest_range_records(log_path)
    source_start = cfg['source']['from_date']
    roots = [item for (start, _), item in records.items() if start == source_start]
    if not roots:
        raise ValueError('No root range was found in range_log.jsonl')

    resolved_end = max(item['end'] for item in roots)
    root_record = max(
        (item for item in roots if item['end'] == resolved_end),
        key=lambda item: item.get('at', ''),
    )
    stack = [(date.fromisoformat(root_record['start']), date.fromisoformat(root_record['end']))]
    terminal = []
    missing_tree_nodes = []
    while stack:
        start, end = stack.pop()
        record = records.get((start.isoformat(), end.isoformat()))
        if record is None:
            missing_tree_nodes.append(f'{start.isoformat()}__{end.isoformat()}')
            continue
        if record.get('status') == 'split':
            left, right = split_range(start, end)
            stack.extend([left, right])
        else:
            terminal.append(record)

    audit_rows = []
    for record in sorted(terminal, key=lambda item: (item['start'], item['end'])):
        raw_path = root / 'data' / 'raw' / 'search_pages' / f"{record['start']}__{record['end']}.html.gz"
        html_link_count = None
        if raw_path.exists():
            with gzip.open(raw_path, 'rt', encoding='utf-8') as f:
                html_link_count = len(set(INCIDENT_LINK_RE.findall(f.read())))
        parsed_count = record.get('parsed_count')
        reported_count = record.get('reported_count')
        audit_rows.append({
            'start': record['start'],
            'end': record['end'],
            'status': record.get('status'),
            'reported_count': reported_count,
            'parsed_count': parsed_count,
            'html_unique_incident_links': html_link_count,
            'html_matches_parsed': html_link_count == parsed_count,
            'reported_matches_parsed': reported_count is None or reported_count == parsed_count,
            'raw_html_present': raw_path.exists(),
        })

    audit = pd.DataFrame(audit_rows)
    metrics = {
        'root_start': root_record['start'],
        'root_end': root_record['end'],
        'terminal_ranges': len(audit),
        'missing_tree_nodes': missing_tree_nodes,
        'non_success_terminal_ranges': int((audit['status'] != 'success').sum()) if not audit.empty else 0,
        'missing_raw_html_ranges': int((~audit['raw_html_present']).sum()) if not audit.empty else 0,
        'html_parse_mismatch_ranges': int((~audit['html_matches_parsed']).sum()) if not audit.empty else 0,
        'reported_parse_mismatch_ranges': int((~audit['reported_matches_parsed']).sum()) if not audit.empty else 0,
        'terminal_parsed_total': int(audit['parsed_count'].fillna(0).sum()) if not audit.empty else 0,
    }
    return audit, metrics


def add_check(checks: list[dict], name: str, passed: bool, message: str, severity: str = 'error', **metrics) -> None:
    checks.append({
        'name': name,
        'status': 'pass' if passed else ('warning' if severity == 'warning' else 'fail'),
        'message': message,
        'metrics': metrics,
    })


def build_category_inventory(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    rows = []
    duplicate_tokens = {}
    total = len(df)
    for column in CATEGORY_COLUMNS:
        counts = Counter()
        duplicate_count = 0
        populated_rows = 0
        for raw in df[column].astype('string').fillna(''):
            values = [value.strip() for value in raw.split('|') if value.strip()]
            if values:
                populated_rows += 1
            if len(values) != len(set(values)):
                duplicate_count += 1
            counts.update(set(values))
        duplicate_tokens[column] = duplicate_count
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
            rows.append({
                'column': column,
                'value': value,
                'incident_count': count,
                'share_all_incidents_pct': round(count / total * 100, 4) if total else 0,
                'share_populated_rows_pct': round(count / populated_rows * 100, 4) if populated_rows else 0,
            })
    return pd.DataFrame(rows), duplicate_tokens


def create_snapshot(root: Path, name: str) -> Path:
    source_csv = root / 'data' / 'processed' / 'oecd_aim_incidents.csv'
    source_parquet = root / 'data' / 'processed' / 'oecd_aim_incidents.parquet'
    source_manifest = root / 'output' / 'manifest.json'
    source_hashes = root / 'output' / 'sha256sums.txt'
    source_log = root / 'data' / 'logs' / 'range_log.jsonl'
    normalization_log = root / 'output' / 'category_normalization_log.csv'
    required_files = [source_csv, source_parquet, source_manifest, source_hashes, source_log]
    missing = [str(path) for path in required_files if not path.exists()]
    if missing:
        raise FileNotFoundError(f'Missing snapshot inputs: {", ".join(missing)}')

    target = root / 'output' / name
    temporary = root / 'output' / f'.{name}.tmp'
    if target.exists() or temporary.exists():
        raise FileExistsError(f'Snapshot path already exists: {target if target.exists() else temporary}')
    temporary.mkdir(parents=True)

    csv_df = pd.read_csv(source_csv, dtype=str, keep_default_na=False)
    parquet_df = pd.read_parquet(source_parquet)
    collector_manifest = json.loads(source_manifest.read_text(encoding='utf-8'))
    cfg = load_config(root / 'config.yaml')
    checks = []

    add_check(checks, 'row_count_match', len(csv_df) == len(parquet_df), 'CSV and Parquet row counts must match.', csv_rows=len(csv_df), parquet_rows=len(parquet_df))
    add_check(checks, 'column_order_match', list(csv_df.columns) == list(parquet_df.columns), 'CSV and Parquet columns and order must match.', csv_columns=list(csv_df.columns), parquet_columns=list(parquet_df.columns))
    same_content = False
    if list(csv_df.columns) == list(parquet_df.columns) and len(csv_df) == len(parquet_df):
        same_content = normalized_frame(csv_df).equals(normalized_frame(parquet_df))
    add_check(checks, 'csv_parquet_content_match', same_content, 'CSV and Parquet values must match after type normalization.')

    missing_required_columns = [column for column in REQUIRED_COLUMNS if column not in csv_df.columns]
    add_check(checks, 'required_columns_present', not missing_required_columns, 'Required columns must be present.', missing_columns=missing_required_columns)
    required_missing = {column: int((~nonempty(csv_df[column])).sum()) for column in REQUIRED_COLUMNS if column in csv_df.columns}
    add_check(checks, 'required_values_present', not any(required_missing.values()), 'Required identifiers, URLs, titles, and dates must be populated.', missing_counts=required_missing)

    duplicate_ids = int(csv_df['incident_id'].duplicated().sum())
    add_check(checks, 'incident_ids_unique', duplicate_ids == 0, 'Incident IDs must be unique.', duplicate_count=duplicate_ids)
    url_mismatches = int(sum(not url.rstrip('/').endswith('/' + incident_id) for incident_id, url in zip(csv_df['incident_id'], csv_df['url'])))
    add_check(checks, 'incident_url_matches_id', url_mismatches == 0, 'Each incident URL must end with its incident ID.', mismatch_count=url_mismatches)

    parsed_dates = pd.to_datetime(csv_df['date'], format='%Y-%m-%d', errors='coerce')
    invalid_dates = int(parsed_dates.isna().sum())
    min_date = parsed_dates.min().date().isoformat() if invalid_dates < len(parsed_dates) else None
    max_date = parsed_dates.max().date().isoformat() if invalid_dates < len(parsed_dates) else None
    add_check(checks, 'dates_valid', invalid_dates == 0, 'All incident dates must use YYYY-MM-DD.', invalid_count=invalid_dates, min_date=min_date, max_date=max_date)

    ui_hits = {column: int(csv_df[column].str.contains(UI_TOKEN_RE, regex=True, na=False).sum()) for column in CATEGORY_COLUMNS}
    add_check(checks, 'no_category_ui_tokens', not any(ui_hits.values()), 'Category columns must not contain Show More or Show Less UI text.', hit_counts=ui_hits)
    inventory, duplicate_tokens = build_category_inventory(csv_df)
    add_check(checks, 'no_duplicate_category_tokens', not any(duplicate_tokens.values()), 'A category value must not repeat within one incident field.', duplicate_row_counts=duplicate_tokens)

    allowed_statuses = {'complete', 'partial_detail_not_found'}
    status_counts = csv_df['category_collection_status'].value_counts(dropna=False).to_dict()
    unknown_statuses = sorted(set(csv_df['category_collection_status']) - allowed_statuses)
    add_check(checks, 'category_statuses_resolved', not unknown_statuses, 'No rows may remain retryable or have an unknown category collection status.', status_counts=status_counts, unknown_statuses=unknown_statuses)
    partial_count = int((csv_df['category_collection_status'] == 'partial_detail_not_found').sum())
    add_check(checks, 'detail_pages_available', partial_count == 0, 'Some OECD detail pages return 404; visible search-card categories are retained.', severity='warning', partial_detail_not_found=partial_count)

    manifest_rows = collector_manifest.get('rows_exported')
    add_check(checks, 'manifest_row_count_match', manifest_rows == len(csv_df), 'Collector manifest row count must match the snapshot.', manifest_rows=manifest_rows, snapshot_rows=len(csv_df))
    manifest_hashes = collector_manifest.get('sha256', {})
    current_csv_hash = sha256(source_csv)
    current_parquet_hash = sha256(source_parquet)
    manifest_csv_hash = manifest_hashes.get(str(source_csv.relative_to(root)))
    manifest_parquet_hash = manifest_hashes.get(str(source_parquet.relative_to(root)))
    add_check(checks, 'collector_hashes_match', manifest_csv_hash == current_csv_hash and manifest_parquet_hash == current_parquet_hash, 'Collector manifest hashes must match current processed files.', csv_match=manifest_csv_hash == current_csv_hash, parquet_match=manifest_parquet_hash == current_parquet_hash)

    range_audit, range_metrics = source_range_audit(root, cfg)
    source_tree_ok = not range_metrics['missing_tree_nodes'] and range_metrics['non_success_terminal_ranges'] == 0
    add_check(checks, 'source_range_tree_complete', source_tree_ok, 'The latest full collection range tree must contain only successful terminal ranges.', **range_metrics)
    source_html_ok = range_metrics['missing_raw_html_ranges'] == 0 and range_metrics['html_parse_mismatch_ranges'] == 0
    add_check(checks, 'source_html_matches_parser', source_html_ok, 'Each terminal range parsed count must match its saved HTML unique incident links.', missing_raw_html_ranges=range_metrics['missing_raw_html_ranges'], mismatch_ranges=range_metrics['html_parse_mismatch_ranges'])
    add_check(checks, 'source_total_matches_dataset', range_metrics['terminal_parsed_total'] == len(csv_df), 'Terminal range parsed totals must equal snapshot rows.', terminal_parsed_total=range_metrics['terminal_parsed_total'], snapshot_rows=len(csv_df))
    add_check(checks, 'source_reported_counts_match', range_metrics['reported_parse_mismatch_ranges'] == 0, 'OECD reported result counts differ from rendered unique incident links in some ranges; saved HTML and parsed rows still match.', severity='warning', mismatch_ranges=range_metrics['reported_parse_mismatch_ranges'])

    missingness_rows = []
    for column in csv_df.columns:
        missing_count = int((~nonempty(csv_df[column])).sum())
        missingness_rows.append({
            'column': column,
            'missing_count': missing_count,
            'missing_pct': round(missing_count / len(csv_df) * 100, 4) if len(csv_df) else 0,
        })
    missingness = pd.DataFrame(missingness_rows)
    partial = csv_df.loc[csv_df['category_collection_status'] == 'partial_detail_not_found', ['incident_id', 'url', 'title', 'date', *CATEGORY_COLUMNS, 'category_collection_status', 'category_collection_error']]

    failures = [check for check in checks if check['status'] == 'fail']
    warnings = [check for check in checks if check['status'] == 'warning']
    validation_status = 'FAIL' if failures else ('PASS_WITH_WARNINGS' if warnings else 'PASS')
    validation_report = {
        'generated_at_utc': utcnow(),
        'status': validation_status,
        'summary': {
            'checks_total': len(checks),
            'passed': sum(check['status'] == 'pass' for check in checks),
            'warnings': len(warnings),
            'failed': len(failures),
            'rows': len(csv_df),
            'date_min': min_date,
            'date_max': max_date,
        },
        'checks': checks,
    }

    shutil.copy2(source_csv, temporary / source_csv.name)
    shutil.copy2(source_parquet, temporary / source_parquet.name)
    shutil.copy2(source_manifest, temporary / 'collector_manifest.json')
    shutil.copy2(source_log, temporary / source_log.name)
    if normalization_log.exists():
        shutil.copy2(normalization_log, temporary / normalization_log.name)
    inventory.to_csv(temporary / 'category_inventory.csv', index=False, encoding='utf-8-sig')
    missingness.to_csv(temporary / 'missingness_report.csv', index=False, encoding='utf-8-sig')
    partial.to_csv(temporary / 'partial_detail_not_found.csv', index=False, encoding='utf-8-sig')
    range_audit.to_csv(temporary / 'source_range_audit.csv', index=False, encoding='utf-8-sig')
    (temporary / 'validation_report.json').write_text(json.dumps(validation_report, ensure_ascii=False, indent=2), encoding='utf-8')

    artifact_hashes = {
        path.name: sha256(path)
        for path in sorted(temporary.iterdir())
        if path.is_file()
    }
    snapshot_manifest = {
        'snapshot_name': name,
        'created_at_utc': utcnow(),
        'status': validation_status,
        'dataset': {
            'rows': len(csv_df),
            'columns': list(csv_df.columns),
            'incident_date_min': min_date,
            'incident_date_max': max_date,
            'source_range_start': range_metrics['root_start'],
            'source_range_end': range_metrics['root_end'],
            'complete_category_rows': int(status_counts.get('complete', 0)),
            'partial_category_rows': partial_count,
        },
        'validation_report': 'validation_report.json',
        'artifacts_sha256': artifact_hashes,
    }
    (temporary / 'manifest.json').write_text(json.dumps(snapshot_manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    all_hashes = {
        path.name: sha256(path)
        for path in sorted(temporary.iterdir())
        if path.is_file()
    }
    (temporary / 'sha256sums.txt').write_text(''.join(f'{digest}  {name}\n' for name, digest in all_hashes.items()), encoding='utf-8')
    temporary.rename(target)

    print(f'Snapshot             : {target}')
    print(f'Validation status    : {validation_status}')
    print(f'Rows                 : {len(csv_df)}')
    print(f'Checks passed        : {sum(check["status"] == "pass" for check in checks)}')
    print(f'Warnings             : {len(warnings)}')
    print(f'Failures             : {len(failures)}')
    print(f'Partial detail rows  : {partial_count}')
    if failures:
        raise RuntimeError('Snapshot was created, but one or more quality checks failed.')
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', default=f'frozen_snapshot_{date.today():%Y%m%d}_candidate')
    args = parser.parse_args()
    create_snapshot(Path.cwd(), args.name)


if __name__ == '__main__':
    main()
