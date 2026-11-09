"""Captura incremental Steam -> Twitch. No interpreta títulos como mensajes de jugadores."""
import argparse
import csv
import datetime as dt
import difflib
import json
import os
import re
import sys
import unicodedata
import uuid
from pathlib import Path
from dotenv import load_dotenv
from .db import connect, payload
from .twitch_api import TwitchAPI

UTC = dt.timezone.utc

def now():
    return dt.datetime.now(UTC).isoformat(timespec='seconds').replace('+00:00','Z')

def normalized(s):
    s = unicodedata.normalize('NFKD',str(s or '').casefold())
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    return ' '.join(re.sub(r'[^a-z0-9]+',' ',s).split())

def similarity(a,b):
    return difflib.SequenceMatcher(None,normalized(a),normalized(b)).ratio()

def safe_int(s):
    try: return int(str(s).strip())
    except (TypeError,ValueError): return None

def load_steam(c,path,ts):
    with open(path,encoding='utf-8-sig',newline='') as f:
        dialect=csv.Sniffer().sniff(f.read(4096),delimiters=';,\t')
        f.seek(0)
        reader=csv.DictReader(f,dialect=dialect)
        if not {'appid','nombre'}.issubset(reader.fieldnames or []):
            raise ValueError('CSV Steam requiere columnas appid y nombre')
        total=0
        for row in reader:
            appid=(row.get('appid') or '').strip()
            name=(row.get('nombre') or '').strip()
            if not appid or not name: continue
            c.execute('''INSERT INTO steam_games VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(steam_appid) DO UPDATE SET steam_name=excluded.steam_name,
            release_date=excluded.release_date,reviews_total=excluded.reviews_total,
            reviews_positive=excluded.reviews_positive,reviews_negative=excluded.reviews_negative,
            verified_utc=excluded.verified_utc,payload_json=excluded.payload_json,updated_utc=excluded.updated_utc''',(
                appid,name,row.get('lanzamiento'),safe_int(row.get('resenas_total')),
                safe_int(row.get('resenas_positivas')),safe_int(row.get('resenas_negativas')),
                row.get('ficha_verificada_utc'),payload(row),ts))
            total += 1
        c.commit()
        return total

def save_game(c,g,source,ts):
    c.execute('''INSERT INTO twitch_games
       (twitch_game_id,twitch_name,box_art_url,discovered_via,updated_utc,payload_json,igdb_id)
       VALUES (?,?,?,?,?,?,?)
       ON CONFLICT(twitch_game_id) DO UPDATE SET twitch_name=excluded.twitch_name,
       box_art_url=excluded.box_art_url,updated_utc=excluded.updated_utc,
       igdb_id=excluded.igdb_id,payload_json=excluded.payload_json''',(
       str(g['id']),g.get('name',''),g.get('box_art_url'),source,ts,payload(g),g.get('igdb_id')))

def match_catalog(c,api,ts,max_fuzzy=None):
    # Buscar todos los nuevos o pendientes, agrupando búsquedas exactas por 100.
    rows=c.execute('''SELECT s.steam_appid,s.steam_name FROM steam_games s
      LEFT JOIN game_matches m ON m.steam_appid=s.steam_appid
      WHERE m.steam_appid IS NULL OR (m.status='unmatched' AND m.checked_utc < ?)
      ORDER BY s.steam_appid''',((dt.datetime.now(UTC)-dt.timedelta(days=7)).isoformat(),)).fetchall()
    stats={'exact':0,'fuzzy':0,'ambiguous':0,'unmatched':0}
    for i in range(0,len(rows),100):
        batch=rows[i:i+100]
        games=api.get('/games',{'name':[r['steam_name'] for r in batch]}).get('data',[])
        by_name={normalized(g.get('name')):g for g in games}
        for row in batch:
            g=by_name.get(normalized(row['steam_name']))
            if not g: continue
            save_game(c,g,'steam_exact',ts)
            c.execute('''INSERT INTO game_matches VALUES (?,?,?,?,?,?)
               ON CONFLICT(steam_appid) DO UPDATE SET twitch_game_id=excluded.twitch_game_id,
               confidence=excluded.confidence,status=excluded.status,method=excluded.method,
               checked_utc=excluded.checked_utc''',
               (row['steam_appid'],str(g['id']),1.0,'matched','exact',ts))
            stats['exact']+=1
        c.commit()
    unmatched=c.execute('''SELECT s.steam_appid,s.steam_name FROM steam_games s
       LEFT JOIN game_matches m ON m.steam_appid=s.steam_appid
       WHERE m.steam_appid IS NULL OR (m.status='unmatched' AND m.checked_utc < ?)
       ORDER BY s.steam_appid''',((dt.datetime.now(UTC)-dt.timedelta(days=7)).isoformat(),)).fetchall()
    if max_fuzzy is not None:
        unmatched = unmatched[:max_fuzzy]
    for row in unmatched:
        candidates,_=api.pages('/search/categories',{'query':row['steam_name']},20)
        ranked=sorted([(similarity(row['steam_name'],g.get('name')),g) for g in candidates],
                      key=lambda x:x[0],reverse=True)
        for rank,(score,g) in enumerate(ranked[:5],1):
            save_game(c,g,'steam_search',ts)
            c.execute('''INSERT INTO match_candidates VALUES (?,?,?,?,?)
               ON CONFLICT(steam_appid,twitch_game_id) DO UPDATE SET
               confidence=excluded.confidence,candidate_rank=excluded.candidate_rank,
               checked_utc=excluded.checked_utc''',
               (row['steam_appid'],str(g['id']),score,rank,ts))
        score,g=(ranked[0] if ranked else (0,None))
        # Auto-aceptación deliberadamente conservadora; coincidencias dudosas pendientes.
        status='matched' if score>=0.95 and (len(ranked)==1 or score-ranked[1][0]>=0.10) else ('ambiguous' if g else 'unmatched')
        gid=str(g['id']) if g and status!='unmatched' else None
        c.execute('''INSERT INTO game_matches VALUES (?,?,?,?,?,?)
          ON CONFLICT(steam_appid) DO UPDATE SET twitch_game_id=excluded.twitch_game_id,
          confidence=excluded.confidence,status=excluded.status,
          method=excluded.method,checked_utc=excluded.checked_utc''',
          (row['steam_appid'],gid,score,status,'fuzzy',ts))
        stats['fuzzy' if status=='matched' else status]+=1
        c.commit()
    return stats

def discover(c,api,ts,max_rows):
    games,_=api.pages('/games/top',{},max_rows)
    for g in games: save_game(c,g,'twitch_top',ts)
    c.commit()
    return len(games)

def choose_games(c, tracked_limit=None, rotation_limit=None, run_number=None):
    """Devuelve TODOS los IDs Twitch confirmados del catálogo Steam, sin selección ni rotación.

    Argumentos anteriores conservados solamente para compatibilidad de llamadas.
    Los juegos sin correspondencia confirmada continúan en steam_games y en el reporte.
    """
    mapped=c.execute("""SELECT DISTINCT m.twitch_game_id FROM game_matches m
      JOIN twitch_games t ON t.twitch_game_id=m.twitch_game_id
      WHERE m.status='matched' AND m.twitch_game_id IS NOT NULL
      ORDER BY m.twitch_game_id""").fetchall()
    ids=[str(r['twitch_game_id']) for r in mapped]
    return ids,len(ids),len(ids)

def store_capture(c,api,gid,capture_id,ts,run_id,max_streams,max_videos,max_clips,clip_hours):
    streams,complete=api.pages('/streams',{'game_id':gid},max_streams)
    for s in streams:
        c.execute('''INSERT OR IGNORE INTO stream_snapshots (capture_id,stream_id,twitch_game_id,captured_utc,user_id,user_name,title,language,started_at,viewer_count,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(
          capture_id,str(s['id']),gid,ts,str(s.get('user_id','')),s.get('user_name'),
          s.get('title'),s.get('language'),s.get('started_at'),int(s.get('viewer_count') or 0),payload(s)))
        c.execute('''UPDATE stream_snapshots SET user_login=?,tags_json=?,thumbnail_url=?,is_mature=?
                     WHERE capture_id=? AND stream_id=?''',
                  (s.get('user_login'),payload(s.get('tags',[])),s.get('thumbnail_url'),
                   int(bool(s.get('is_mature'))) if s.get('is_mature') is not None else None,
                   capture_id,str(s['id'])))
    viewers=sum(int(s.get('viewer_count') or 0) for s in streams)
    c.execute('''INSERT OR REPLACE INTO game_snapshots VALUES (?,?,?,?,?,?,?)''',(
      capture_id,gid,ts,viewers,len(streams),int(complete),run_id))
    videos,_=api.pages('/videos',{'game_id':gid,'sort':'time'},max_videos)
    for v in videos:
        vid=str(v['id'])
        c.execute('''INSERT INTO twitch_videos (video_id,twitch_game_id,user_id,title,description,language,created_at,published_at,duration,video_type,url,first_seen_utc,last_seen_utc,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(video_id) DO UPDATE SET last_seen_utc=excluded.last_seen_utc,
        payload_json=excluded.payload_json,title=excluded.title,description=excluded.description''',(
          vid,gid,str(v.get('user_id','')),v.get('title'),v.get('description'),v.get('language'),
          v.get('created_at'),v.get('published_at'),v.get('duration'),v.get('type'),v.get('url'),ts,ts,payload(v)))
        c.execute('''UPDATE twitch_videos SET user_name=?,stream_id=?,thumbnail_url=?,viewable=?
                     WHERE video_id=?''', (v.get('user_name'),v.get('stream_id'),
                     v.get('thumbnail_url'),v.get('viewable'),vid))
        c.execute('INSERT OR IGNORE INTO video_observations VALUES (?,?,?,?)',(
          vid,capture_id,ts,int(v.get('view_count') or 0)))
    end=dt.datetime.fromisoformat(ts.replace('Z','+00:00'))
    start=end-dt.timedelta(hours=clip_hours)
    clips,_=api.pages('/clips',{'game_id':gid,
        'started_at':start.isoformat(timespec='seconds').replace('+00:00','Z'),
        'ended_at':ts},max_clips)
    for cl in clips:
        clipid=str(cl['id'])
        c.execute('''INSERT INTO twitch_clips (clip_id,twitch_game_id,broadcaster_id,creator_id,title,language,created_at,duration_seconds,url,first_seen_utc,last_seen_utc,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(clip_id) DO UPDATE SET last_seen_utc=excluded.last_seen_utc,
        payload_json=excluded.payload_json,title=excluded.title''',(
          clipid,gid,str(cl.get('broadcaster_id','')),str(cl.get('creator_id','')),
          cl.get('title'),cl.get('language'),cl.get('created_at'),cl.get('duration'),
          cl.get('url'),ts,ts,payload(cl)))
        c.execute('''UPDATE twitch_clips SET creator_name=?,broadcaster_name=?,video_id=?,
                   vod_offset=?,is_featured=?,thumbnail_url=? WHERE clip_id=?''',
                  (cl.get('creator_name'),cl.get('broadcaster_name'),cl.get('video_id'),
                   cl.get('vod_offset'),int(bool(cl.get('is_featured'))) if cl.get('is_featured') is not None else None,
                   cl.get('thumbnail_url'),clipid))
        c.execute('INSERT OR IGNORE INTO clip_observations VALUES (?,?,?,?)',(
          clipid,capture_id,ts,int(cl.get('view_count') or 0)))
    c.commit()
    return len(streams),len(videos),len(clips),complete

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--steam',default='data/steam_maestro_20261103.csv')
    p.add_argument('--db',default='state/geca_twitch.sqlite3')
    p.add_argument('--tracked-limit',type=int,default=None,help='Obsoleto: todos los juegos se monitorean')
    p.add_argument('--rotation-limit',type=int,default=None,help='Obsoleto: no se rota el catálogo')
    p.add_argument('--fuzzy-limit',type=int,default=None,help='Solo para diagnóstico; por defecto sin límite')
    p.add_argument('--discover-limit',type=int,default=0,help='Descubrimiento adicional fuera del maestro; 0 deshabilita')
    p.add_argument('--max-streams',type=int,default=None,help='Sin límite local; paginación Helix')
    p.add_argument('--max-videos',type=int,default=500,help='Máximo ~500 por juego de Helix')
    p.add_argument('--max-clips',type=int,default=None,help='Sin límite local en intervalo de captura')
    p.add_argument('--clip-hours',type=int,default=8)
    a=p.parse_args(argv)
    load_dotenv()  # Local: .env; Actions: secretos en variables de entorno.
    ts=now()
    run_id=os.environ.get('GITHUB_RUN_ID',uuid.uuid4().hex)+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
    c=connect(a.db)
    c.execute('INSERT INTO runs(run_id,started_utc,status) VALUES (?,?,?)',(run_id,ts,'running'))
    c.commit()
    total=load_steam(c,a.steam,ts)
    api=TwitchAPI()
    failed=[]
    try:
        matches=match_catalog(c,api,ts,a.fuzzy_limit)
        discovered=discover(c,api,ts,a.discover_limit) if a.discover_limit else 0
        nrun=c.execute('SELECT count(*) FROM runs').fetchone()[0]
        games,total_matched,stable=choose_games(c,a.tracked_limit,a.rotation_limit,nrun)
        statuses={row[0]:row[1] for row in c.execute('''SELECT COALESCE(m.status,'pending'),COUNT(*)
              FROM steam_games s LEFT JOIN game_matches m ON s.steam_appid=m.steam_appid
              GROUP BY COALESCE(m.status,'pending')''')}
        print(json.dumps({'steam_rows_loaded':total,'steam_games_total':c.execute('SELECT COUNT(*) FROM steam_games').fetchone()[0],
            'matching_this_run':matches,'match_status_counts':statuses,'twitch_top':discovered,
            'distinct_matched_twitch_games':total_matched,'capturing_all_matched':len(games)},ensure_ascii=False),flush=True)
        for j,gid in enumerate(games,1):
            try:
                # Una marca temporal común por juego; su ID identifica el intento sin colisiones.
                cap=run_id+':'+gid
                counts=store_capture(c,api,gid,cap,now(),run_id,
                                     a.max_streams,a.max_videos,a.max_clips,a.clip_hours)
                print(f'[{j}/{len(games)}] {gid}: streams={counts[0]} videos={counts[1]} clips={counts[2]} complete={counts[3]}',flush=True)
            except Exception as exc:
                failed.append({'game_id':gid,'error':str(exc)[:300]})
                print(f'[ERROR] {gid}: {exc}',file=sys.stderr,flush=True)
        succ=len(games)-len(failed)
        status='success' if not failed else ('partial' if succ else 'failed')
        c.execute('''UPDATE runs SET finished_utc=?,status=?,games_attempted=?,
           games_succeeded=?,games_failed=?,error_summary=? WHERE run_id=?''',
           (now(),status,len(games),succ,len(failed),payload(failed)[:4000],run_id))
        c.commit()
        if failed: raise RuntimeError(f'{len(failed)} juegos fallaron; revisar logs')
    except Exception as exc:
        c.execute('UPDATE runs SET finished_utc=?,status=?,error_summary=? WHERE run_id=? AND status=\'running\'',
                  (now(),'failed',str(exc)[:4000],run_id))
        c.commit()
        raise
    finally: c.close()

if __name__=='__main__':
    main()
