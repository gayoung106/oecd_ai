from __future__ import annotations
import gzip, hashlib, json, platform
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
from requests import HTTPError
from tqdm import tqdm
from .http import build_session, get
from .parser import parse_result_count, parse_cards, parse_detail_categories

UI_TOKEN_RE = re.compile(r'\bShow\s+(?:More|Less)(?:\s*\(\d+\))?\b', re.I)
CATEGORY_COLUMNS = ['ai_principles','industries','affected_stakeholders','harm_types','business_function','ai_system_task']

def utcnow(): return datetime.now(timezone.utc).isoformat()

def append_jsonl(path,obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a',encoding='utf-8') as f: f.write(json.dumps(obj,ensure_ascii=False)+'\n')

def ensure_dirs(root):
    for d in [root/'data'/'raw'/'search_pages', root/'data'/'raw'/'detail_pages', root/'data'/'processed', root/'data'/'logs', root/'output']:
        d.mkdir(parents=True, exist_ok=True)

def build_params(start,end,cfg):
    return {
        'search_terms':'[]','and_condition':'false','from_date':start.isoformat(),'to_date':end.isoformat(),
        'properties_config':json.dumps({'principles':[],'industries':[],'harm_types':[],'harm_levels':[],'harmed_entities':[],'business_functions':[],'ai_tasks':[],'autonomy_levels':[],'languages':[]},separators=(',',':')),
        'order_by':cfg['source'].get('order_by','date'),'num_results':int(cfg['source'].get('num_results',100)),
    }

def load_range_statuses(path):
    statuses={}
    if not path.exists(): return statuses
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            x=json.loads(line)
            if x.get('start') and x.get('end'):
                statuses[(x['start'],x['end'])]=x.get('status')
        except Exception: pass
    return statuses

def load_failed_detail_ids(path):
    failed=set()
    if not path.exists(): return failed
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            x=json.loads(line)
            if x.get('status')=='detail_failed' and x.get('incident_id'):
                failed.add(str(x['incident_id']))
        except Exception: pass
    return failed

def save_html(root,start,end,html):
    p=search_html_path(root,start,end)
    with gzip.open(p,'wt',encoding='utf-8') as f: f.write(html)

def search_html_path(root,start,end):
    return root/'data'/'raw'/'search_pages'/f'{start.isoformat()}__{end.isoformat()}.html.gz'

def load_saved_range(root,start,end,cfg):
    path=search_html_path(root,start,end)
    if not path.exists(): return None
    with gzip.open(path,'rt',encoding='utf-8') as f:
        html=f.read()
    return parse_result_count(html),parse_cards(html,cfg['source']['base_url'])

def save_detail_html(root,incident_id,html):
    p=root/'data'/'raw'/'detail_pages'/f'{incident_id}.html.gz'
    with gzip.open(p,'wt',encoding='utf-8') as f: f.write(html)

def fetch_detail_categories(session,cfg,root,row):
    r=get(session,row['url'],cfg)
    if cfg['crawl'].get('save_detail_html_gzip',True):
        save_detail_html(root,row['incident_id'],r.text)
    fields=parse_detail_categories(r.text)
    missing=[field for field in row.get('_collapsed_category_fields',[]) if not fields.get(field)]
    if missing:
        raise ValueError(f'Detail page did not contain collapsed categories: {", ".join(missing)}')
    return fields

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

def count_ui_token_rows(df):
    cols=[c for c in CATEGORY_COLUMNS if c in df.columns]
    if not cols:
        return 0
    hits=df[cols].fillna('').apply(lambda col: col.str.contains(UI_TOKEN_RE, regex=True))
    return int(hits.any(axis=1).sum())

def normalize_category_value(value):
    if pd.isna(value):
        return value
    values=[item.strip() for item in str(value).split('|') if item.strip()]
    return ' | '.join(dict.fromkeys(values))

def export(root,rows,cfg):
    cleaned_rows=[]
    for row in rows.values():
        cleaned={k:v for k,v in row.items() if not k.startswith('_')}
        cleaned_rows.append(cleaned)
    df=pd.DataFrame(cleaned_rows)
    for column in CATEGORY_COLUMNS:
        if column in df.columns:
            df[column]=df[column].map(normalize_category_value)
    if not df.empty and 'date' in df.columns:
        df=df.sort_values(['date','incident_id'],ascending=[False,True])
    if 'article_count' in df.columns:
        df['article_count']=pd.to_numeric(df['article_count'],errors='coerce').astype('Int64')
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
    resume=cfg['crawl'].get('resume',True)
    range_statuses=load_range_statuses(log_path) if resume else {}
    rows={}
    csv_path=root/'data'/'processed'/'oecd_aim_incidents.csv'
    if resume and csv_path.exists():
        old=pd.read_csv(csv_path,dtype=str,keep_default_na=False)
        bad_rows=count_ui_token_rows(old)
        if bad_rows:
            raise ValueError(f'Existing CSV contains Show More/Show Less UI tokens in {bad_rows} rows. Re-run with --force to rebuild from source pages and detail pages.')
        tracks_status='category_collection_status' in old.columns
        failed_detail_ids=load_failed_detail_ids(log_path) if not tracks_status else set()
        for x in old.to_dict(orient='records'):
            incident_id=str(x.get('incident_id',''))
            if not incident_id:
                continue
            if not tracks_status:
                x['category_collection_status']='retry_required' if incident_id in failed_detail_ids else 'complete'
                x['category_collection_error']=''
            rows[incident_id]=x
    if cfg['crawl'].get('retry_incomplete_only',False):
        pending_statuses={'failed','detail_incomplete'}
        stack=[
            (date.fromisoformat(s),date.fromisoformat(e))
            for (s,e),status in range_statuses.items()
            if status in pending_statuses
        ]
        if not stack:
            print('No incomplete ranges found in the collection log.')
    else:
        stack=[(start,end)]
    stats={'ranges_requested':0,'ranges_cached':0,'ranges_split':0,'ranges_skipped':0,'single_day_overflows':0,'http_failures':0,'detail_pages_requested':0,'detail_not_found':0,'detail_failures':0}
    pbar=tqdm(desc='Collecting AIM date ranges',unit='range')
    while stack:
        s,e=stack.pop(); key=(s.isoformat(),e.isoformat())
        previous_status=range_statuses.get(key)
        if previous_status=='success':
            stats['ranges_skipped']+=1; pbar.update(1); continue
        if previous_status=='split' and s<e:
            left,right=split_range(s,e); stack.append(left); stack.append(right)
            stats['ranges_skipped']+=1; pbar.update(1); continue
        try:
            saved=load_saved_range(root,s,e,cfg) if cfg['crawl'].get('retry_incomplete_only',False) else None
            if saved is not None:
                count,parsed=saved; stats['ranges_cached']+=1
            else:
                _,count,parsed=fetch_range(session,cfg,root,s,e); stats['ranges_requested']+=1
        except Exception as ex:
            stats['http_failures']+=1; append_jsonl(log_path,{'start':s.isoformat(),'end':e.isoformat(),'status':'failed','error':f'{type(ex).__name__}: {ex}','at':utcnow()}); pbar.update(1); continue
        actual=len(parsed); cap=int(cfg['source'].get('num_results',100)); needs_split=((count is not None and count>cap) or actual>=cap)
        if needs_split and s<e:
            left,right=split_range(s,e); stats['ranges_split']+=1; stack.append(left); stack.append(right)
            append_jsonl(log_path,{'start':s.isoformat(),'end':e.isoformat(),'status':'split','reported_count':count,'parsed_count':actual,'at':utcnow()}); pbar.update(1); continue
        if needs_split and s==e:
            stats['single_day_overflows']+=1; append_jsonl(overflow_path,{'date':s.isoformat(),'reported_count':count,'parsed_count':actual,'note':'Single-day result count reached/exceeded UI cap; follow-up required.','at':utcnow()})
        range_detail_failures=0
        for row in parsed:
            if row.get('_collapsed_category_fields') and cfg['crawl'].get('fetch_detail_for_collapsed_categories',True):
                existing=rows.get(str(row['incident_id']))
                if existing and existing.get('category_collection_status')=='complete':
                    for field in CATEGORY_COLUMNS:
                        row[field]=existing.get(field)
                    row['category_collection_status']='complete'
                    row['category_collection_error']=''
                else:
                    stats['detail_pages_requested']+=1
                    try:
                        detail_fields=fetch_detail_categories(session,cfg,root,row)
                        row.update(detail_fields)
                        row['category_collection_status']='complete'
                        row['category_collection_error']=''
                    except HTTPError as ex:
                        if ex.response is not None and ex.response.status_code==404:
                            stats['detail_not_found']+=1
                            row['category_collection_status']='partial_detail_not_found'
                            row['category_collection_error']='Detail page returned HTTP 404; visible search-card categories retained.'
                            append_jsonl(log_path,{'incident_id':row.get('incident_id'),'url':row.get('url'),'status':'detail_not_found','error':str(ex),'at':utcnow()})
                        else:
                            stats['detail_failures']+=1
                            range_detail_failures+=1
                            row['category_collection_status']='retry_required'
                            row['category_collection_error']=f'{type(ex).__name__}: {ex}'
                            append_jsonl(log_path,{'incident_id':row.get('incident_id'),'url':row.get('url'),'status':'detail_failed','error':row['category_collection_error'],'at':utcnow()})
                    except Exception as ex:
                        stats['detail_failures']+=1
                        range_detail_failures+=1
                        row['category_collection_status']='retry_required'
                        row['category_collection_error']=f'{type(ex).__name__}: {ex}'
                        append_jsonl(log_path,{'incident_id':row.get('incident_id'),'url':row.get('url'),'status':'detail_failed','error':row['category_collection_error'],'at':utcnow()})
            else:
                row['category_collection_status']='complete'
                row['category_collection_error']=''
            row['collected_at_utc']=utcnow(); row['source_range_start']=s.isoformat(); row['source_range_end']=e.isoformat(); rows[str(row['incident_id'])]=row
        range_status='success' if range_detail_failures==0 else 'detail_incomplete'
        append_jsonl(log_path,{'start':s.isoformat(),'end':e.isoformat(),'status':range_status,'reported_count':count,'parsed_count':actual,'detail_failures':range_detail_failures,'at':utcnow()})
        export(root,rows,cfg); pbar.update(1)
    pbar.close(); df=export(root,rows,cfg); write_manifest(root,cfg,started,df,stats)
    print('\n[DONE]'); print(f'Rows exported        : {len(df)}'); print(f'Ranges requested     : {stats["ranges_requested"]}'); print(f'Ranges from cache    : {stats["ranges_cached"]}'); print(f'Ranges split         : {stats["ranges_split"]}'); print(f'Ranges skipped       : {stats["ranges_skipped"]}'); print(f'Single-day overflows : {stats["single_day_overflows"]}'); print(f'HTTP failures        : {stats["http_failures"]}'); print(f'Detail pages         : {stats["detail_pages_requested"]}'); print(f'Detail 404 (partial) : {stats["detail_not_found"]}'); print(f'Detail failures      : {stats["detail_failures"]}'); print(f'CSV                  : {root / "data" / "processed" / "oecd_aim_incidents.csv"}'); print(f'Manifest             : {root / "output" / "manifest.json"}')
    if stats['http_failures'] or stats['detail_failures']:
        raise RuntimeError('Collection finished with failures. Re-run without --force to retry incomplete ranges.')
