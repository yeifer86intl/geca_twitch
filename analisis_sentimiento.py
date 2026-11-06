#!/usr/bin/env python3
"""OPTIONAL multilingual language-model sentiment analysis on public stream/video/clip TITLES.
This is a framing proxy, NOT actual viewer opinions. Requires transformers + torch.
"""
import argparse, csv, sqlite3
from collections import defaultdict

def main():
 p=argparse.ArgumentParser()
 p.add_argument('--db',default='geca_twitch.sqlite')
 p.add_argument('--out',default='salida_twitch/sentimiento_titulos.csv')
 p.add_argument('--model',default='cardiffnlp/twitter-xlm-roberta-base-sentiment')
 p.add_argument('--limit',type=int,default=0)
 args=p.parse_args()
 try:
  from transformers import pipeline
 except ImportError:
  raise SystemExit('Instala: pip install transformers torch')
 conn=sqlite3.connect(args.db)
 conn.execute('CREATE TABLE IF NOT EXISTS sentiment_titles (source TEXT NOT NULL, item_id TEXT NOT NULL, twitch_game_id TEXT, title TEXT, sentiment_label TEXT, score REAL, model TEXT, PRIMARY KEY(source,item_id,model))')
 # Use stream_id+captured_utc, avoiding overwriting changing titles from older captures.
 sql='''SELECT 'stream',stream_id||'|'||captured_utc,twitch_game_id,title FROM stream_snapshots WHERE TRIM(COALESCE(title,''))<>''
 UNION ALL SELECT 'video',video_id,twitch_game_id,title FROM twitch_videos WHERE TRIM(COALESCE(title,''))<>''
 UNION ALL SELECT 'clip',clip_id,twitch_game_id,title FROM twitch_clips WHERE TRIM(COALESCE(title,''))<>'' '''
 rows=conn.execute(sql).fetchall()
 if args.limit:rows=rows[:args.limit]
 classifier=pipeline('text-classification',model=args.model,tokenizer=args.model, truncation=True)
 for start in range(0,len(rows),16):
  batch=rows[start:start+16];pred=classifier([r[3][:1200] for r in batch],batch_size=16)
  for (source,item_id,gid,title),r in zip(batch,pred):
   conn.execute('INSERT OR REPLACE INTO sentiment_titles VALUES(?,?,?,?,?,?,?)',(source,item_id,gid,title,r['label'],float(r['score']),args.model))
  conn.commit()
  print(f'Analyzed {min(start+16,len(rows))}/{len(rows)}')
 from pathlib import Path
 out=Path(args.out);out.parent.mkdir(exist_ok=True,parents=True)
 with out.open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.writer(f);w.writerow(['source','item_id','twitch_game_id','title','sentiment_label','score','model'])
  w.writerows(conn.execute('SELECT * FROM sentiment_titles WHERE model=?',(args.model,)))
 print('Result:',out)
 print('IMPORTANT: Scores describe sentiment expressed in stream/video/clip titles, not player acceptance.')

if __name__=='__main__':main()
