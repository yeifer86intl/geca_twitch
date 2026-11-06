#!/usr/bin/env python3
"""GECA Radar: Twitch Helix -> SQLite, with auditable Steam matching.
Environment: TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET. Do not commit credentials.
"""
import argparse, csv, datetime as dt, difflib, json, os, re, sqlite3, sys, time, unicodedata
from pathlib import Path
import requests

BASE='https://api.twitch.tv/helix'
DB_SCHEMA='''
CREATE TABLE IF NOT EXISTS steam_games (steam_appid TEXT PRIMARY KEY, steam_name TEXT NOT NULL, release_date TEXT, snapshot_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS twitch_games (twitch_game_id TEXT PRIMARY KEY, twitch_name TEXT NOT NULL, igdb_id TEXT, box_art_url TEXT, first_seen_utc TEXT NOT NULL, last_seen_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS game_matches (steam_appid TEXT PRIMARY KEY REFERENCES steam_games(steam_appid), twitch_game_id TEXT REFERENCES twitch_games(twitch_game_id), steam_name TEXT, twitch_name TEXT, similarity REAL, status TEXT NOT NULL, rationale TEXT, checked_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stream_snapshots (captured_utc TEXT NOT NULL, capture_date_utc TEXT NOT NULL, twitch_game_id TEXT NOT NULL, stream_id TEXT NOT NULL, user_id TEXT, user_name TEXT, title TEXT, language TEXT, viewers INTEGER, started_at TEXT, PRIMARY KEY(captured_utc,stream_id));
CREATE TABLE IF NOT EXISTS game_snapshots (captured_utc TEXT NOT NULL, capture_date_utc TEXT NOT NULL, twitch_game_id TEXT NOT NULL, total_viewers INTEGER NOT NULL, active_streams INTEGER NOT NULL, is_complete INTEGER NOT NULL, scope TEXT NOT NULL, PRIMARY KEY(captured_utc,twitch_game_id,scope));
CREATE TABLE IF NOT EXISTS twitch_videos (video_id TEXT PRIMARY KEY, twitch_game_id TEXT, title TEXT, description TEXT, user_id TEXT, user_name TEXT, created_at TEXT, published_at TEXT, url TEXT, view_count INTEGER, language TEXT, duration TEXT, captured_utc TEXT);
CREATE TABLE IF NOT EXISTS twitch_clips (clip_id TEXT PRIMARY KEY, twitch_game_id TEXT, title TEXT, broadcaster_id TEXT, broadcaster_name TEXT, created_at TEXT, url TEXT, view_count INTEGER, language TEXT, duration REAL, captured_utc TEXT);
CREATE TABLE IF NOT EXISTS collection_runs (run_id TEXT PRIMARY KEY, started_utc TEXT, finished_utc TEXT, steam_candidates INTEGER, matched INTEGER, ambiguous INTEGER, unmatched INTEGER, errors INTEGER, notes TEXT);
CREATE INDEX IF NOT EXISTS idx_snap ON game_snapshots(twitch_game_id,capture_date_utc);
CREATE INDEX IF NOT EXISTS idx_stream_game ON stream_snapshots(twitch_game_id,capture_date_utc);
'''

def now():return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
def normalize(s):
 s=unicodedata.normalize('NFKD',str(s or '')).casefold()
 s=''.join(ch for ch in s if not unicodedata.combining(ch))
 s=re.sub(r'[^a-z0-9]+',' ',s)
 return ' '.join(s.split())
def similarity(a,b):
 a,b=normalize(a),normalize(b)
 if not a or not b:return 0.
 if a==b:return 1.
 return difflib.SequenceMatcher(None,a,b).ratio()

def load_steam(path,limit):
 with open(path,encoding='utf-8-sig',newline='') as f:
  sample=f.read(10000);f.seek(0)
  dialect=csv.Sniffer().sniff(sample,delimiters=';,\t')
  rows=list(csv.DictReader(f,dialect=dialect))
 required={'appid','nombre'}
 if not rows or not required.issubset(rows[0]):raise ValueError('CSV necesita appid y nombre (maestro.csv GECA).')
 items=[];seen=set()
 for row in rows:
  appid=str(row['appid']).strip();name=str(row['nombre']).strip()
  if not appid or not name or appid in seen:continue
  items.append((appid,name,str(row.get('lanzamiento',''))));seen.add(appid)
  if limit and len(items)>=limit:break
 return items

class Twitch:
 def __init__(self):
  cid=os.getenv('TWITCH_CLIENT_ID');secret=os.getenv('TWITCH_CLIENT_SECRET')
  if not cid or not secret:raise RuntimeError('Configura TWITCH_CLIENT_ID y TWITCH_CLIENT_SECRET en variables de entorno.')
  self.s=requests.Session();self.cid=cid;self.secret=secret;self.token=None
  self.auth()
 def auth(self):
  r=self.s.post('https://id.twitch.tv/oauth2/token',data={'client_id':self.cid,'client_secret':self.secret,'grant_type':'client_credentials'},timeout=30)
  r.raise_for_status();self.token=r.json()['access_token']
 def get(self,endpoint,params=None):
  for retry in range(6):
   try:r=self.s.get(BASE+endpoint,params=params or {},headers={'Client-Id':self.cid,'Authorization':'Bearer '+self.token},timeout=35)
   except requests.RequestException:
    if retry==5:raise
    time.sleep(min(2**retry,20));continue
   if r.status_code==401 and retry==0:self.auth();continue
   if r.status_code==429:
    reset=r.headers.get('Ratelimit-Reset');delay=max(1,min(90,int(reset)-int(time.time())+1)) if reset and reset.isdigit() else min(2**retry,30)
    time.sleep(delay);continue
   if r.status_code>=500 and retry<5:time.sleep(2**retry);continue
   r.raise_for_status();return r.json()
  raise RuntimeError(f'No fue posible consultar {endpoint}')
 def pages(self,endpoint,params=None,max_rows=500):
  params=dict(params or {});cursor=None;found=[];seen=set()
  while len(found)<max_rows:
   args={**params,'first':min(100,max_rows-len(found))}
   if cursor:args['after']=cursor
   data=self.get(endpoint,args)
   for row in data.get('data',[]):
    ident=row.get('id')
    if ident in seen:continue
    seen.add(ident);found.append(row)
   next_cursor=data.get('pagination',{}).get('cursor')
   if not next_cursor or next_cursor==cursor or not data.get('data'):break
   cursor=next_cursor
  return found

def put_game(db,g,t):
 db.execute('''INSERT INTO twitch_games VALUES(?,?,?,?,?,?) ON CONFLICT(twitch_game_id) DO UPDATE SET twitch_name=excluded.twitch_name,igdb_id=excluded.igdb_id,box_art_url=excluded.box_art_url,last_seen_utc=excluded.last_seen_utc''',(str(g['id']),g['name'],g.get('igdb_id'),g.get('box_art_url'),t,t))

def match_games(api,db,steam,t,threshold):
 matches=[];stats={'matched':0,'ambiguous':0,'unmatched':0};records=[]
 for n,(appid,name,release) in enumerate(steam,1):
  db.execute('INSERT INTO steam_games VALUES(?,?,?,?) ON CONFLICT(steam_appid) DO UPDATE SET steam_name=excluded.steam_name,release_date=excluded.release_date,snapshot_utc=excluded.snapshot_utc',(appid,name,release,t))
  try:
   # Get Games permits exact names, Search Categories retrieves alternatives.
   exact=api.get('/games',{'name':name}).get('data',[])
   candidate=exact or api.pages('/search/categories',{'query':name},max_rows=100)
   for g in candidate:put_game(db,g,t)
   ranked=sorted(((similarity(name,g['name']),g) for g in candidate),key=lambda x:x[0],reverse=True)
   best_score,best=(ranked[0] if ranked else (0,None))
   second_score=ranked[1][0] if len(ranked)>1 else 0
   if best and best_score>=threshold and (best_score==1 or best_score-second_score>=.04):status='matched'
   elif best and best_score>=threshold:status='ambiguous'
   else:status='unmatched'
   rationale=('exact normalized name' if best_score==1 else 'fuzzy name only; verify edition/series' if best else 'no Twitch candidate')
   tid=str(best['id']) if best and status in ('matched','ambiguous') else None
   db.execute('''INSERT INTO game_matches VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(steam_appid) DO UPDATE SET twitch_game_id=excluded.twitch_game_id,steam_name=excluded.steam_name,twitch_name=excluded.twitch_name,similarity=excluded.similarity,status=excluded.status,rationale=excluded.rationale,checked_utc=excluded.checked_utc''',(appid,tid,name,best['name'] if best else None,round(best_score,5),status,rationale,t))
   stats[status]+=1
   if status=='matched':matches.append((appid,tid))
   records.append({'steam_appid':appid,'steam_name':name,'twitch_game_id':tid,'twitch_name':best['name'] if best else '', 'score':round(best_score,4),'status':status})
  except Exception as exc:
   stats['unmatched']+=1;records.append({'steam_appid':appid,'steam_name':name,'twitch_game_id':'','twitch_name':'','score':'','status':'error: '+str(exc)[:120]})
  if n%25==0:db.commit();print(f'Matching {n}/{len(steam)}',flush=True)
 db.commit();return matches,records,stats

def capture_game(api,db,gid,t,scope,max_streams,max_videos,max_clips):
 day=t[:10];streams=api.pages('/streams',{'game_id':gid},max_rows=max_streams)
 # Complete means exhausted rather than capped. If the number exactly equals cap, mark incomplete conservatively.
 complete=int(len(streams)<max_streams)
 for s in streams:
  db.execute('INSERT OR IGNORE INTO stream_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)',(t,day,gid,str(s['id']),s.get('user_id'),s.get('user_name'),s.get('title'),s.get('language'),int(s.get('viewer_count') or 0),s.get('started_at')))
 db.execute('INSERT OR REPLACE INTO game_snapshots VALUES(?,?,?,?,?,?,?)',(t,day,gid,sum(int(s.get('viewer_count') or 0) for s in streams),len(streams),complete,scope))
 if max_videos:
  for v in api.pages('/videos',{'game_id':gid,'sort':'time','period':'week'},max_rows=min(500,max_videos)):
   db.execute('INSERT INTO twitch_videos VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET view_count=excluded.view_count,captured_utc=excluded.captured_utc',(str(v['id']),gid,v.get('title'),v.get('description'),v.get('user_id'),v.get('user_name'),v.get('created_at'),v.get('published_at'),v.get('url'),int(v.get('view_count') or 0),v.get('language'),v.get('duration'),t))
 if max_clips:
  end=dt.datetime.now(dt.timezone.utc);start=end-dt.timedelta(days=7)
  for c in api.pages('/clips',{'game_id':gid,'started_at':start.isoformat(timespec='seconds').replace('+00:00','Z'),'ended_at':end.isoformat(timespec='seconds').replace('+00:00','Z')},max_rows=max_clips):
   db.execute('INSERT INTO twitch_clips VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(clip_id) DO UPDATE SET view_count=excluded.view_count,captured_utc=excluded.captured_utc',(str(c['id']),gid,c.get('title'),c.get('broadcaster_id'),c.get('broadcaster_name'),c.get('created_at'),c.get('url'),int(c.get('view_count') or 0),c.get('language'),float(c.get('duration') or 0),t))
 db.commit()

def export(db,out):
 out.mkdir(parents=True,exist_ok=True)
 queries={
  'steam_twitch_matches.csv':'SELECT * FROM game_matches ORDER BY status,similarity DESC',
  'twitch_games.csv':'SELECT g.*,CASE WHEN EXISTS(SELECT 1 FROM game_matches m WHERE m.twitch_game_id=g.twitch_game_id AND m.status="matched") THEN 0 ELSE 1 END AS without_confirmed_steam_match FROM twitch_games g',
  'twitch_daily_snapshots.csv':'SELECT * FROM game_snapshots ORDER BY capture_date_utc,twitch_game_id',
  'twitch_stream_snapshots.csv':'SELECT * FROM stream_snapshots',
  'twitch_videos.csv':'SELECT * FROM twitch_videos',
  'twitch_clips.csv':'SELECT * FROM twitch_clips',
 }
 for filename,sql in queries.items():
  cursor=db.execute(sql)
  with (out/filename).open('w',encoding='utf-8-sig',newline='') as f:
   w=csv.writer(f);w.writerow([x[0] for x in cursor.description]);w.writerows(cursor)

def alerts(db,out):
 # EXACT GECA rule: 14 uninterrupted calendar days, same scope, previous 7 daily mean >0, +500 and +50%.
 # Multiple same-day snapshots use latest complete snapshot only; no faking absent days with zeros.
 day=dt.datetime.now(dt.timezone.utc).date()
 records=[]
 for gid,name in db.execute('SELECT twitch_game_id,twitch_name FROM twitch_games'):
  for scope in ['matched_steam','twitch_discovery']:
   vals=[]
   for offset in range(13,-1,-1):
    current=(day-dt.timedelta(days=offset)).isoformat()
    v=db.execute('SELECT total_viewers,is_complete,captured_utc FROM game_snapshots WHERE twitch_game_id=? AND capture_date_utc=? AND scope=? ORDER BY captured_utc DESC LIMIT 1',(gid,current,scope)).fetchone()
    vals.append(v if v and v[1] else None)
   if any(v is None for v in vals):continue
   prev=sum(v[0] for v in vals[:7])/7;recent=sum(v[0] for v in vals[7:])/7
   if prev>0 and recent-prev>=500 and (recent-prev)/prev>=.5:
    records.append((gid,name,scope,round(prev,2),round(recent,2),round(recent-prev,2),round(100*(recent-prev)/prev,2),day.isoformat()))
 with (out/'alertas_atencion.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.writer(f);w.writerow(['twitch_game_id','twitch_name','scope','mean_previous_7d','mean_recent_7d','delta_viewers','growth_pct','cutoff_utc']);w.writerows(records)
 return len(records)

def main():
 p=argparse.ArgumentParser(description='GECA Steam x Twitch daily capture')
 p.add_argument('--steam',required=True,help='GECA maestro.csv with appid;nombre')
 p.add_argument('--db',default='geca_twitch.sqlite');p.add_argument('--out',default='salida_twitch')
 p.add_argument('--max-steam',type=int,default=0,help='0 = all catalog')
 p.add_argument('--top-games',type=int,default=100,help='Twitch top game discovery cap; 0 disables')
 p.add_argument('--max-streams',type=int,default=1000)
 p.add_argument('--max-videos',type=int,default=30);p.add_argument('--max-clips',type=int,default=30)
 p.add_argument('--max-capture-games',type=int,default=0,help='0 = all selected; for testing use 10')
 p.add_argument('--match-threshold',type=float,default=.90)
 p.add_argument('--skip-matching',action='store_true',help='Use already-confirmed matches in existing DB')
 args=p.parse_args();steam=load_steam(args.steam,args.max_steam);api=Twitch();t=now();db=sqlite3.connect(args.db);db.executescript(DB_SCHEMA)
 stats={'matched':0,'ambiguous':0,'unmatched':0};err=0
 if not args.skip_matching or not db.execute('SELECT 1 FROM game_matches LIMIT 1').fetchone():
  matched,records,stats=match_games(api,db,steam,t,args.match_threshold)
 else:
  matched=db.execute('SELECT steam_appid,twitch_game_id FROM game_matches WHERE status="matched"').fetchall()
 discovered=[]
 if args.top_games:
  try:
   for g in api.pages('/games/top',max_rows=args.top_games):put_game(db,g,t);discovered.append(str(g['id']))
   db.commit()
  except Exception as e:print('Warning discovery:',e,file=sys.stderr);err+=1
 selected={gid:'matched_steam' for _,gid in matched}
 # Prioritize Twitch discovery without losing confirmed Steam games.
 selected={**selected, **{gid:('matched_steam' if gid in selected else 'twitch_discovery') for gid in discovered}}
 # selected games can be captured once even if they appear in both sets
 if args.max_capture_games:selected=dict(list(selected.items())[:args.max_capture_games])
 for i,(gid,scope) in enumerate(selected.items(),1):
  try:capture_game(api,db,gid,t,scope,args.max_streams,args.max_videos,args.max_clips)
  except Exception as e:err+=1;print(f'Error capturing game_id={gid}: {e}',file=sys.stderr)
  if i%10==0:print(f'Captured {i}/{len(selected)} categories',flush=True)
 out=Path(args.out);export(db,out);nalerts=alerts(db,out)
 db.execute('INSERT OR REPLACE INTO collection_runs VALUES(?,?,?,?,?,?,?,?,?)',(t,t,now(),len(steam),stats['matched'],stats['ambiguous'],stats['unmatched'],err,f'{len(selected)} captured categories; {nalerts} attention alerts; scope=UTC point-in-time'))
 db.commit();print(json.dumps({'steam_candidates':len(steam),'matching':stats,'categories_selected':len(selected),'errors':err,'attention_alerts':nalerts,'db':args.db,'exports':str(out)},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
