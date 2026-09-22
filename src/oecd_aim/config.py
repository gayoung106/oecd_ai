from pathlib import Path
import yaml

def load_config(path: Path) -> dict:
    with path.open('r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    for k in ('source', 'crawl', 'output'):
        if k not in cfg:
            raise ValueError(f'Missing config section: {k}')
    return cfg
