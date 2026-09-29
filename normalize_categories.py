from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.oecd_aim.collector import CATEGORY_COLUMNS, normalize_category_value, sha256


def main() -> None:
    root = Path.cwd()
    csv_path = root / 'data' / 'processed' / 'oecd_aim_incidents.csv'
    parquet_path = root / 'data' / 'processed' / 'oecd_aim_incidents.parquet'
    manifest_path = root / 'output' / 'manifest.json'
    log_path = root / 'output' / 'category_normalization_log.csv'

    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    changes = []
    for column in CATEGORY_COLUMNS:
        for index, original in df[column].items():
            normalized = normalize_category_value(original)
            if normalized != original:
                changes.append({
                    'incident_id': df.at[index, 'incident_id'],
                    'column': column,
                    'original_value': original,
                    'normalized_value': normalized,
                    'normalization': 'duplicate tokens removed; first occurrence order retained',
                })
                df.at[index, column] = normalized

    csv_temp = csv_path.with_suffix('.csv.tmp')
    parquet_temp = parquet_path.with_suffix('.parquet.tmp')
    df.to_csv(csv_temp, index=False, encoding='utf-8-sig')
    parquet_df = df.copy()
    parquet_df['article_count'] = pd.to_numeric(parquet_df['article_count'], errors='coerce').astype('Int64')
    parquet_df.to_parquet(parquet_temp, index=False)
    csv_temp.replace(csv_path)
    parquet_temp.replace(parquet_path)
    pd.DataFrame(changes).to_csv(log_path, index=False, encoding='utf-8-sig')

    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    transform = {
        'name': 'deduplicate_category_tokens',
        'applied_at_utc': datetime.now(timezone.utc).isoformat(),
        'changed_rows': len({item['incident_id'] for item in changes}),
        'changed_cells': len(changes),
        'audit_log': str(log_path.relative_to(root)),
    }
    transforms = [item for item in manifest.get('post_collection_transforms', []) if item.get('name') != transform['name']]
    manifest['post_collection_transforms'] = [*transforms, transform]
    manifest['rows_exported'] = len(df)
    manifest['columns'] = list(df.columns)
    targets = [csv_path, parquet_path, root / 'data' / 'logs' / 'range_log.jsonl', log_path]
    manifest['sha256'] = {str(path.relative_to(root)): sha256(path) for path in targets if path.exists()}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'output' / 'sha256sums.txt').write_text(
        ''.join(f'{digest}  {path}\n' for path, digest in manifest['sha256'].items()),
        encoding='utf-8',
    )
    print(f'Changed rows         : {transform["changed_rows"]}')
    print(f'Changed cells        : {transform["changed_cells"]}')
    print(f'Normalization log    : {log_path}')


if __name__ == '__main__':
    main()
