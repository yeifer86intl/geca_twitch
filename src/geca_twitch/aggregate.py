"""Agregados GECA. Solo alerta con 14 días UTC anteriores íntegros y comparables."""
import argparse
import csv
import datetime as dt
from pathlib import Path
from .db import connect

UTC=dt.timezone.utc

def utc_day(d):
    return d.strftime('%Y-%m-%d')

def aggregate(c,asof=None):
    asof=asof or dt.datetime.now(UTC).date()
    # Recalcular histórico desde observaciones, sin promediar promedios por canal.
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
    c.execute('DELETE FROM attention_alerts')
    start=asof-dt.timedelta(days=14)
    eligible=0
    alerts=0
    # Evaluación hasta ayer UTC, sin el día parcial actual.
    gids=[r[0] for r in c.execute('SELECT DISTINCT twitch_game_id FROM game_snapshots')]
    for gid in gids:
        day_metrics=c.execute('''SELECT utc_day,snapshot_count,avg_viewers,streams_complete
           FROM daily_metrics WHERE twitch_game_id=? AND utc_day>=? AND utc_day<?''',
           (gid,utc_day(start),utc_day(asof))).fetchall()
        by_day={r['utc_day']:r for r in day_metrics}
        values=[]
        ok=True
        for i in range(14):
            day=utc_day(start+dt.timedelta(days=i))
            r=by_day.get(day)
            # Cron a las 00:17/04:17/... 20:17; deben existir las 6 franjas.
            if r is None or r['streams_complete']!=1:
                ok=False; break
            slots={int(x[0][11:13])//4 for x in c.execute('''SELECT captured_utc
              FROM game_snapshots WHERE twitch_game_id=? AND substr(captured_utc,1,10)=?''',
              (gid,day)).fetchall()}
            if slots!={0,1,2,3,4,5}:
                ok=False; break
            values.append(float(r['avg_viewers']))
        if not ok: continue
        prior=sum(values[:7])/7
        recent=sum(values[7:])/7
        if prior <= 0: continue
        eligible+=1
        delta=recent-prior
        growth=delta/prior*100
        if delta >= 500 and growth>=50:
            c.execute('INSERT INTO attention_alerts VALUES (?,?,?,?,?,?,?)',
                      (utc_day(asof),gid,prior,recent,delta,growth,1))
            alerts+=1
    c.commit()
    return {'games_evaluable':eligible,'alerts':alerts,'asof_utc':utc_day(asof),
            'rule':'14 días completos, 6 franjas UTC/día, +500 espectadores y +50%'}

def export_csv(c,folder):
    root=Path(folder); root.mkdir(parents=True,exist_ok=True)
    queries={
      'steam_twitch_matches.csv':'''SELECT s.steam_appid,s.steam_name,m.twitch_game_id,
          t.twitch_name,m.confidence,m.status,m.method,m.checked_utc
          FROM steam_games s LEFT JOIN game_matches m ON s.steam_appid=m.steam_appid
          LEFT JOIN twitch_games t ON t.twitch_game_id=m.twitch_game_id
          ORDER BY s.steam_name''',
      'twitch_only.csv':'''SELECT t.twitch_game_id,t.twitch_name,t.discovered_via,t.updated_utc
          FROM twitch_games t WHERE NOT EXISTS
          (SELECT 1 FROM game_matches m WHERE m.twitch_game_id=t.twitch_game_id AND m.status='matched')
          ORDER BY t.twitch_name''',
      'game_metrics_daily.csv':'''SELECT d.*,t.twitch_name FROM daily_metrics d
          LEFT JOIN twitch_games t ON d.twitch_game_id=t.twitch_game_id ORDER BY d.utc_day,d.twitch_game_id''',
      'attention_alerts.csv':'''SELECT a.*,t.twitch_name FROM attention_alerts a
          LEFT JOIN twitch_games t ON a.twitch_game_id=t.twitch_game_id ORDER BY a.delta_viewers DESC''',
      'steam_reception.csv':'''SELECT steam_appid,steam_name,reviews_total,reviews_positive,reviews_negative,
          CASE WHEN reviews_positive IS NOT NULL AND reviews_negative IS NOT NULL
          AND (reviews_positive+reviews_negative)>0 THEN
          ROUND(100.0*reviews_positive/(reviews_positive+reviews_negative),2) END
          AS steam_positive_pct FROM steam_games ORDER BY steam_name''',
      'runs.csv':'SELECT * FROM runs ORDER BY started_utc DESC',
    }
    for filename,sql in queries.items():
        cur=c.execute(sql)
        with open(root/filename,'w',newline='',encoding='utf-8-sig') as f:
            out=csv.writer(f); out.writerow([x[0] for x in cur.description]);out.writerows(cur)

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--db',default='state/geca_twitch.sqlite3')
    p.add_argument('--out',default='outputs')
    a=p.parse_args(argv)
    c=connect(a.db)
    print(aggregate(c))
    export_csv(c,a.out)
    c.close()

if __name__=='__main__':main()
