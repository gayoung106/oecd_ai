import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def build_session(cfg):
    retry = Retry(total=int(cfg['crawl'].get('max_retries',5)), connect=int(cfg['crawl'].get('max_retries',5)), read=int(cfg['crawl'].get('max_retries',5)), backoff_factor=float(cfg['crawl'].get('backoff_factor',1.5)), status_forcelist=(429,500,502,503,504), allowed_methods=frozenset(['GET']), respect_retry_after_header=True, raise_on_status=False)
    s = requests.Session()
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.headers.update({'User-Agent': cfg['crawl']['user_agent'], 'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language':'en-US,en;q=0.9'})
    return s

def get(session, url, cfg, params=None):
    r = session.get(url, params=params, timeout=float(cfg['crawl'].get('timeout_seconds',45)), allow_redirects=True)
    r.raise_for_status()
    delay = float(cfg['crawl'].get('delay_seconds',1.0))
    if delay:
        time.sleep(delay)
    return r
