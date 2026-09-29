from __future__ import annotations
import argparse
from pathlib import Path
from datetime import date, timedelta
from src.oecd_aim.config import load_config
from src.oecd_aim.collector import run_collection

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--days', type=int, default=None, help='Test: collect only the most recent N days')
    ap.add_argument('--force', action='store_true', help='Ignore resume state and refetch')
    ap.add_argument('--retry-incomplete', action='store_true', help='Retry only failed or detail-incomplete ranges from the log')
    args = ap.parse_args()
    if args.force and args.retry_incomplete:
        ap.error('--force and --retry-incomplete cannot be used together')
    cfg = load_config(Path(args.config))
    if args.force:
        cfg['crawl']['resume'] = False
    if args.days:
        end = date.today()
        start = end - timedelta(days=args.days - 1)
        cfg['source']['from_date'] = start.isoformat()
        cfg['source']['to_date'] = end.isoformat()
    if args.retry_incomplete:
        cfg['crawl']['resume'] = True
        cfg['crawl']['retry_incomplete_only'] = True
    run_collection(cfg, Path.cwd())

if __name__ == '__main__':
    main()
