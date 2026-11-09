"""Registra la decisión humana para una propuesta existente."""
import argparse
import datetime as dt
from .db import connect

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--db',default='state/geca_twitch.sqlite3')
    p.add_argument('--day',required=True)
    p.add_argument('--game-key',required=True,help='twitch:<id> o steam:<appid>')
    p.add_argument('--decision',required=True,choices=['analizar','seguir','descartar','posponer'])
    p.add_argument('--note',default='')
    p.add_argument('--analyst',default='analista')
    args=p.parse_args(argv)
    c=connect(args.db)
    try:
        row=c.execute('''SELECT 1 FROM editorial_proposals WHERE proposal_day=? AND game_key=?''',
                      (args.day,args.game_key)).fetchone()
        if not row:p.error(f'No existe propuesta para {args.day} y {args.game_key}; revisa outputs/editorial_proposals.csv')
        ts=dt.datetime.now(dt.timezone.utc).isoformat()
        c.execute('''INSERT INTO editorial_decisions
           (proposal_day,game_key,decision,note,decided_by,decided_utc)
           VALUES (?,?,?,?,?,?)''',(args.day,args.game_key,args.decision,args.note,args.analyst,ts))
        c.execute('''UPDATE editorial_proposals SET status=? WHERE proposal_day=? AND game_key=?''',
                  (args.decision,args.day,args.game_key))
        c.commit()
        print('Decisión guardada:',args.game_key,args.decision)
    finally:c.close()
if __name__=='__main__':main()
