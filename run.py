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
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    if args.force:
        cfg['crawl']['resume'] = False
    if args.days:
        end = date.today()
        start = end - timedelta(days=args.days - 1)
        cfg['source']['from_date'] = start.isoformat()
        cfg['source']['to_date'] = end.isoformat()
    run_collection(cfg, Path.cwd())

if __name__ == '__main__':
    main()
