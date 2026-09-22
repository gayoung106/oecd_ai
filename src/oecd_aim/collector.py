from __future__ import annotations
import gzip, hashlib, json, platform
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
from tqdm import tqdm
from .http import build_session, get
from .parser import parse_result_count, parse_cards

def utcnow(): return datetime.now(timezone.utc).isoformat()

def append_jsonl(path,obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a',encoding='utf-8') as f: f.write(json.dumps(obj,ensure_ascii=False)+'\n')

def ensure_dirs(root):
    for d in [root/'data'/'raw'/'search_pages', root/'data'/'processed', root/'data'/'logs', root/'output']:
        d.mkdir(parents=True, exist_ok=True)

def build_params(start,end,cfg):
    return {
        'search_terms':'[]','and_condition':'false','from_date':start.isoformat(),'to_date':end.isoformat(),
        'properties_config':json.dumps({'principles':[],'industries':[],'harm_types':[],'harm_levels':[],'harmed_entities':[],'business_functions':[],'ai_tasks':[],'autonomy_levels':[],'languages':[]},separators=(',',':')),
        'order_by':cfg['source'].get('order_by','date'),'num_results':int(cfg['source'].get('num_results',100)),
    }

def load_done_ranges(path):
    done=set()
    if not path.exists(): return done
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            x=json.loads(line)
            if x.get('status')=='success': done.add((x['start'],x['end']))
        except Exception: pass
    return done

def save_html(root,start,end,html):
    p=root/'data'/'raw'/'search_pages'/f'{start.isoformat()}__{end.isoformat()}.html.gz'
    with gzip.open(p,'wt',encoding='utf-8') as f: f.write(html)

def fetch_range(session,cfg,root,start,end):
    r=get(session,cfg['source']['base_url'],cfg,params=build_params(start,end,cfg))
    if cfg['crawl'].get('save_search_html_gzip',True): save_html(root,start,end,r.text)
    return r, parse_result_count(r.text), parse_cards(r.text,cfg['source']['base_url'])

def split_range(start,end):
    days=(end-start).days
    mid=start+timedelta(days=days//2)
    return (start,mid),(mid+timedelta(days=1),end)

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def export(root,rows,cfg):
    df=pd.DataFrame(list(rows.values()))
    if not df.empty and 'date' in df.columns:
        df=df.sort_values(['date','incident_id'],ascending=[False,True])
    out=root/'data'/'processed'
    if cfg['output'].get('csv',True): df.to_csv(out/'oecd_aim_incidents.csv',index=False,encoding='utf-8-sig')
    if cfg['output'].get('parquet',True): df.to_parquet(out/'oecd_aim_incidents.parquet',index=False)
    return df

def write_manifest(root,cfg,started,df,stats):
    targets=[root/'data'/'processed'/'oecd_aim_incidents.csv',root/'data'/'processed'/'oecd_aim_incidents.parquet',root/'data'/'logs'/'range_log.jsonl',root/'data'/'logs'/'overflow_single_day.jsonl']
    hashes={}
    for p in targets:
        if p.exists(): hashes[str(p.relative_to(root))]=sha256(p)
    (root/'output'/'sha256sums.txt').write_text(''.join(f'{v}  {k}\n' for k,v in hashes.items()),encoding='utf-8')
    manifest={'project':'OECD AIM Collector v2','started_at_utc':started,'finished_at_utc':utcnow(),'source_url':cfg['source']['base_url'],'source_period':{'from_date':cfg['source']['from_date'],'to_date':cfg['source'].get('to_date')},'rows_exported':int(len(df)),'columns':list(df.columns),'stats':stats,'config_snapshot':cfg,'python_version':platform.python_version(),'sha256':hashes}
    (root/'output'/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')

def run_collection(cfg,root):
    ensure_dirs(root)
    started=utcnow()
    start=date.fromisoformat(cfg['source']['from_date'])
    end=date.fromisoformat(cfg['source']['to_date']) if cfg['source'].get('to_date') else date.today()
    if start>end: raise ValueError('from_date is after to_date')
    session=build_session(cfg)
    log_path=root/'data'/'logs'/'range_log.jsonl'; overflow_path=root/'data'/'logs'/'overflow_single_day.jsonl'
    done=load_done_ranges(log_path) if cfg['crawl'].get('resume',True) else set()
    rows={}
    csv_path=root/'data'/'processed'/'oecd_aim_incidents.csv'
    if cfg['crawl'].get('resume',True) and csv_path.exists():
        old=pd.read_csv(csv_path,dtype=str,keep_default_na=False)
        rows={str(x['incident_id']):x for x in old.to_dict(orient='records') if x.get('incident_id')}
    stack=[(start,end)]
    stats={'ranges_requested':0,'ranges_split':0,'ranges_skipped':0,'single_day_overflows':0,'http_failures':0}
    pbar=tqdm(desc='Collecting AIM date ranges',unit='range')
    while stack:
        s,e=stack.pop(); key=(s.isoformat(),e.isoformat())
        if key in done:
            stats['ranges_skipped']+=1; pbar.update(1); continue
        try:
            r,count,parsed=fetch_range(session,cfg,root,s,e); stats['ranges_requested']+=1
        except Exception as ex:
            stats['http_failures']+=1; append_jsonl(log_path,{'start':s.isoformat(),'end':e.isoformat(),'status':'failed','error':f'{type(ex).__name__}: {ex}','at':utcnow()}); pbar.update(1); continue
        actual=len(parsed); cap=int(cfg['source'].get('num_results',100)); needs_split=((count is not None and count>cap) or actual>=cap)
        if needs_split and s<e:
            left,right=split_range(s,e); stats['ranges_split']+=1; stack.append(left); stack.append(right)
            append_jsonl(log_path,{'start':s.isoformat(),'end':e.isoformat(),'status':'split','reported_count':count,'parsed_count':actual,'at':utcnow()}); pbar.update(1); continue
        if needs_split and s==e:
            stats['single_day_overflows']+=1; append_jsonl(overflow_path,{'date':s.isoformat(),'reported_count':count,'parsed_count':actual,'note':'Single-day result count reached/exceeded UI cap; follow-up required.','at':utcnow()})
        for row in parsed:
            row['collected_at_utc']=utcnow(); row['source_range_start']=s.isoformat(); row['source_range_end']=e.isoformat(); rows[str(row['incident_id'])]=row
        append_jsonl(log_path,{'start':s.isoformat(),'end':e.isoformat(),'status':'success','reported_count':count,'parsed_count':actual,'at':utcnow()})
        export(root,rows,cfg); pbar.update(1)
    pbar.close(); df=export(root,rows,cfg); write_manifest(root,cfg,started,df,stats)
    print('\n[DONE]'); print(f'Rows exported        : {len(df)}'); print(f'Ranges requested     : {stats["ranges_requested"]}'); print(f'Ranges split         : {stats["ranges_split"]}'); print(f'Single-day overflows : {stats["single_day_overflows"]}'); print(f'HTTP failures        : {stats["http_failures"]}'); print(f'CSV                  : {root / "data" / "processed" / "oecd_aim_incidents.csv"}'); print(f'Manifest             : {root / "output" / "manifest.json"}')
