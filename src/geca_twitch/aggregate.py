# GECA: snapshots -> métricas diarias -> evaluación auditable -> hasta 3 propuestas.
# No descarga ni procesa archivos multimedia. No clasifica sentimiento de Twitch.
import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from .db import connect
from .releases import get_launch_candidates

UTC = dt.timezone.utc

def utc_day(d):
    return d.isoformat()

def aggregate(c, asof=None):
    asof = asof or dt.datetime.now(UTC).date()
    checked = dt.datetime.now(UTC).isoformat(timespec='seconds')
    c.execute('DELETE FROM daily_metrics')
    c.execute('''INSERT INTO daily_metrics
      (utc_day,twitch_game_id,snapshot_count,avg_viewers,max_viewers,
       avg_streams,streams_complete,new_videos,new_clips)
      SELECT substr(gs.captured_utc,1,10),gs.twitch_game_id,COUNT(*),
       AVG(gs.viewers_observed),MAX(gs.viewers_observed),AVG(gs.streams_observed),
       MIN(gs.streams_complete),
       (SELECT COUNT(*) FROM twitch_videos v WHERE v.twitch_game_id=gs.twitch_game_id
        AND substr(v.created_at,1,10)=substr(gs.captured_utc,1,10)),
       (SELECT COUNT(*) FROM twitch_clips cl WHERE cl.twitch_game_id=gs.twitch_game_id
        AND substr(cl.created_at,1,10)=substr(gs.captured_utc,1,10))
      FROM game_snapshots gs GROUP BY substr(gs.captured_utc,1,10),gs.twitch_game_id''')
    c.execute('DELETE FROM attention_alerts WHERE evaluated_utc_day=?',(utc_day(asof),))
    c.execute('DELETE FROM attention_evaluations WHERE evaluated_utc_day=?',(utc_day(asof),))
    c.execute('DELETE FROM editorial_proposals WHERE proposal_day=? AND status=\'pending\'',(utc_day(asof),))
    c.execute('DELETE FROM geca_signals WHERE proposal_day=?',(utc_day(asof),))
    start = asof - dt.timedelta(days=14)
    gids = [r[0] for r in c.execute('SELECT DISTINCT twitch_game_id FROM game_snapshots')]
    results = []
    for gid in gids:
        # Una única medición diaria comparable, cercana a las 12:17 UTC.
        # Ante reejecuciones manuales, elegir la muestra más próxima a la hora objetivo.
        # Las muestras anteriores (4/6 horas) no se suman: tampoco aumentan la ponderación.
        values=[]; reason='ok'; valid=0; captured_minutes=[]
        for i in range(14):
            day=utc_day(start+dt.timedelta(days=i))
            snapshots=c.execute("""SELECT captured_utc, viewers_observed, streams_complete
                FROM game_snapshots WHERE twitch_game_id=?
                AND substr(captured_utc,1,10)=? ORDER BY captured_utc""",(gid,day)).fetchall()
            if not snapshots:
                reason='missing_day';break
            eligible_snapshots=[]
            for snap in snapshots:
                try:
                    moment=dt.datetime.fromisoformat(snap['captured_utc'].replace('Z','+00:00'))
                    minute=moment.hour*60+moment.minute+moment.second/60
                except (ValueError,TypeError,AttributeError):
                    continue
                # Ventana ±90 minutos en torno a las 12:17 UTC (08:17 Venezuela).
                if abs(minute-(12*60+17))<=90:
                    eligible_snapshots.append((abs(minute-(12*60+17)),minute,snap))
            if not eligible_snapshots:
                reason='missing_daily_window';break
            _,minute,snap=min(eligible_snapshots,key=lambda x:(x[0],x[1]))
            if snap['streams_complete']!=1:
                reason='incomplete_streams';break
            captured_minutes.append(minute)
            values.append(float(snap['viewers_observed']));valid+=1
        if reason=='ok' and max(captured_minutes)-min(captured_minutes)>90:
            reason='non_comparable_capture_times'
        prior=recent=delta=growth=None
        eligible=0; alerted=0
        if reason=='ok':
            prior=sum(values[:7])/7
            recent=sum(values[7:])/7
            if prior <= 0:
                reason='zero_previous_average'
            else:
                eligible=1
                delta=recent-prior
                growth=100*delta/prior
                alerted=int(delta>=500 and growth>=50)
                if not alerted:reason='threshold_not_met'
        c.execute('''INSERT INTO attention_evaluations
          (evaluated_utc_day,twitch_game_id,prior_avg,recent_avg,delta_viewers,growth_pct,
           eligible,alerted,reason,days_valid,checked_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
          (utc_day(asof),gid,prior,recent,delta,growth,eligible,alerted,reason,valid,checked))
        if alerted:
            c.execute('''INSERT INTO attention_alerts VALUES (?,?,?,?,?,?,?)''',
                      (utc_day(asof),gid,prior,recent,delta,growth,1))
            results.append((gid,prior,recent,delta,growth))
    # Máximo 3 propuestas combinadas. Prioridad actual: señales Twitch, luego estrenos Steam.
    # Ambas reglas siguen siendo independientes: el sentimiento NO activa ninguna.
    proposal_candidates=[]
    for gid,prior,recent,delta,growth in sorted(results,key=lambda x:(-x[3],x[0])):
        name_row=c.execute('SELECT twitch_name FROM twitch_games WHERE twitch_game_id=?',(gid,)).fetchone()
        name=name_row['twitch_name'] if name_row else gid
        evidence={'source':'Twitch','rule':'attention_growth','window_start_utc':utc_day(start),
            'window_end_utc':utc_day(asof-dt.timedelta(days=1)),
            'previous_avg_viewers':round(prior,2),'recent_avg_viewers':round(recent,2),
            'delta_viewers':round(delta,2),'growth_pct':round(growth,2),
            'coverage':'14 consecutive UTC days, 1 comparable daily capture around 12:17 UTC, complete streams'}
        proposal_candidates.append((f'twitch:{gid}',name,'attention_growth',evidence,
            '¿Qué explica el aumento de atención y merece revisión editorial?'))
    for game in get_launch_candidates(c,asof):
        evidence={'source':'Steam','rule':'upcoming_release','steam_appid':game['steam_appid'],
           'release_date':game['release_date'],'verified_utc':game['verified_utc'],
           'days_until_release':game['days_until_release']}
        proposal_candidates.append((f"steam:{game['steam_appid']}",game['steam_name'],
            'upcoming_release',evidence,
            '¿Conviene revisar el posicionamiento editorial antes del lanzamiento?'))
    # Mantener TODAS las señales, incluso si solo se proponen tres.
    # Unificar señales Steam/Twitch sobre la misma identidad cuando está confirmada.
    grouped={}
    for game_key,name,rule,evidence,question in proposal_candidates:
        canonical=game_key
        if game_key.startswith('twitch:'):
            gid=game_key.split(':',1)[1]
            match=c.execute("SELECT steam_appid FROM game_matches WHERE twitch_game_id=? AND status='matched' ORDER BY steam_appid LIMIT 1",(gid,)).fetchone()
            if match:canonical='steam:'+str(match['steam_appid'])
        c.execute('''INSERT OR REPLACE INTO geca_signals
          (proposal_day,game_key,rule,evidence_json,created_utc) VALUES (?,?,?,?,?)''',
          (utc_day(asof),canonical,rule,json.dumps(evidence,ensure_ascii=False),checked))
        group=grouped.setdefault(canonical,{'name':name,'rules':[],'evidence':{},'questions':[]})
        group['rules'].append(rule);group['evidence'][rule]=evidence
        group['questions'].append(question)
    # Priorización operativa y transparente: doble señal primero, luego aumento
    # absoluto, y finalmente cercanía del lanzamiento. NO es un ranking editorial.
    def priority(item):
        key,v=item
        twitch=v['evidence'].get('attention_growth',{})
        release=v['evidence'].get('upcoming_release',{})
        return (-len(v['rules']),-float(twitch.get('delta_viewers') or 0),
                release.get('days_until_release') if release.get('days_until_release') is not None else 999999,key)
    selected=sorted(grouped.items(),key=priority)[:3]
    for game_key,entry in selected:
        rule='+'.join(sorted(entry['rules']))
        c.execute('''INSERT OR IGNORE INTO editorial_proposals
            (proposal_day,game_key,game_name,rule,evidence_json,question,status,created_utc)
            VALUES (?,?,?,?,?,?,?,?)''',
            (utc_day(asof),game_key,entry['name'],rule,
             json.dumps(entry['evidence'],ensure_ascii=False),
             ' '.join(dict.fromkeys(entry['questions'])),'pending',checked))
    c.commit()
    return {'games_evaluable':sum(1 for r in c.execute('SELECT 1 FROM attention_evaluations WHERE evaluated_utc_day=? AND eligible=1',(utc_day(asof),))),
        'alerts':len(results),'proposals':min(3,len(grouped)),'asof_utc':utc_day(asof),
        'rule':'14 días completos, 1 captura diaria comparable (12:17 UTC), +500 espectadores y +50%'}

def export_csv(c,folder):
    root=Path(folder);root.mkdir(parents=True,exist_ok=True)
    queries={
      'steam_catalog_coverage.csv':'''SELECT s.steam_appid,s.steam_name,
          COALESCE(m.status,'pending') AS match_status,m.confidence,m.method,
          m.checked_utc,m.twitch_game_id,t.twitch_name,
          (SELECT MAX(gs.captured_utc) FROM game_snapshots gs
           WHERE gs.twitch_game_id=m.twitch_game_id) AS last_capture_utc,
          (SELECT COUNT(*) FROM game_snapshots gs
           WHERE gs.twitch_game_id=m.twitch_game_id) AS total_captures,
          CASE WHEN m.status='matched' AND m.twitch_game_id IS NOT NULL AND
                    EXISTS (SELECT 1 FROM game_snapshots gs WHERE gs.twitch_game_id=m.twitch_game_id)
               THEN 'captured_on_twitch'
               WHEN m.status='matched' AND m.twitch_game_id IS NOT NULL
               THEN 'matched_awaiting_capture'
               WHEN m.status='ambiguous' THEN 'requires_identity_review'
               WHEN m.status='unmatched' THEN 'not_found_on_twitch'
               ELSE 'pending_identity_search' END AS monitoring_status
          FROM steam_games s LEFT JOIN game_matches m ON s.steam_appid=m.steam_appid
          LEFT JOIN twitch_games t ON t.twitch_game_id=m.twitch_game_id
          ORDER BY s.steam_name''',
      'steam_twitch_matches.csv':'''SELECT s.steam_appid,s.steam_name,m.twitch_game_id,
          t.twitch_name,t.igdb_id,m.confidence,m.status,m.method,m.checked_utc
          FROM steam_games s LEFT JOIN game_matches m ON s.steam_appid=m.steam_appid
          LEFT JOIN twitch_games t ON t.twitch_game_id=m.twitch_game_id ORDER BY s.steam_name''',
      'twitch_only.csv':'''SELECT t.twitch_game_id,t.twitch_name,t.igdb_id,t.discovered_via,t.updated_utc
          FROM twitch_games t WHERE NOT EXISTS
          (SELECT 1 FROM game_matches m WHERE m.twitch_game_id=t.twitch_game_id AND m.status='matched')
          ORDER BY t.twitch_name''',
      'game_metrics_daily.csv':'''SELECT d.*,t.twitch_name,t.igdb_id FROM daily_metrics d
          LEFT JOIN twitch_games t ON d.twitch_game_id=t.twitch_game_id ORDER BY d.utc_day,d.twitch_game_id''',
      'attention_alerts.csv':'''SELECT a.*,t.twitch_name FROM attention_alerts a
          LEFT JOIN twitch_games t ON t.twitch_game_id=a.twitch_game_id ORDER BY a.delta_viewers DESC''',
      'attention_evaluations.csv':'''SELECT e.*,t.twitch_name FROM attention_evaluations e
          LEFT JOIN twitch_games t ON t.twitch_game_id=e.twitch_game_id ORDER BY e.evaluated_utc_day,e.twitch_game_id''',
      'editorial_proposals.csv':'SELECT * FROM editorial_proposals ORDER BY proposal_day,game_key',
      'geca_all_signals.csv':'SELECT * FROM geca_signals ORDER BY proposal_day,game_key',
      'editorial_decisions.csv':'SELECT * FROM editorial_decisions ORDER BY decided_utc DESC',
      'twitch_video_links.csv':'''SELECT v.video_id,v.twitch_game_id,g.twitch_name,v.title,v.description,
          v.created_at,v.duration,v.language,v.url,v.user_id,v.user_name,
          o.view_count,o.captured_utc AS last_observed_utc
          FROM twitch_videos v LEFT JOIN twitch_games g ON v.twitch_game_id=g.twitch_game_id
          LEFT JOIN video_observations o ON o.video_id=v.video_id AND o.rowid=(
             SELECT x.rowid FROM video_observations x WHERE x.video_id=v.video_id ORDER BY x.captured_utc DESC LIMIT 1)
          ORDER BY v.created_at DESC''',
      'twitch_clip_links.csv':'''SELECT cl.clip_id,cl.twitch_game_id,g.twitch_name,cl.title,
          cl.created_at,cl.url,cl.creator_name,cl.broadcaster_name,cl.video_id,cl.vod_offset,
          cl.is_featured,o.view_count,o.captured_utc AS last_observed_utc
          FROM twitch_clips cl LEFT JOIN twitch_games g ON cl.twitch_game_id=g.twitch_game_id
          LEFT JOIN clip_observations o ON o.clip_id=cl.clip_id AND o.rowid=(
             SELECT x.rowid FROM clip_observations x WHERE x.clip_id=cl.clip_id ORDER BY x.captured_utc DESC LIMIT 1)
          ORDER BY cl.created_at DESC''',
      'steam_reception.csv':'''SELECT steam_appid,steam_name,reviews_total,reviews_positive,reviews_negative,
          CASE WHEN reviews_positive IS NOT NULL AND reviews_negative IS NOT NULL
          AND (reviews_positive+reviews_negative)>0 THEN
          ROUND(100.0*reviews_positive/(reviews_positive+reviews_negative),2) END
          AS steam_positive_pct FROM steam_games ORDER BY steam_name''',
      'runs.csv':'SELECT * FROM runs ORDER BY started_utc DESC'
    }
    for filename,sql in queries.items():
        cur=c.execute(sql)
        with open(root/filename,'w',newline='',encoding='utf-8-sig') as f:
            writer=csv.writer(f);writer.writerow([x[0] for x in cur.description]);writer.writerows(cur)

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default='state/geca_twitch.sqlite3')
    parser.add_argument('--out',default='outputs')
    a=parser.parse_args(argv)
    c=connect(a.db)
    print(aggregate(c))
    export_csv(c,a.out)
    c.close()

if __name__=='__main__':main()
