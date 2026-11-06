"""Pruebas sin red/credenciales."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from geca_twitch.db import connect
from geca_twitch.ingest import normalized, similarity, load_steam, save_game, store_capture, choose_games
from geca_twitch.aggregate import aggregate, export_csv

class FakeAPI:
    def pages(self,endpoint,params,max_rows):
        if endpoint=='/streams':
            return ([{'id':'s1','user_id':'u1','user_name':'a','title':'Juego live',
                     'language':'es','started_at':'2026-11-05T00:00:00Z','viewer_count':1200}],True)
        if endpoint=='/videos':
            return ([{'id':'v1','user_id':'u1','title':'Video','description':'',
                     'language':'es','created_at':'2026-11-05T00:00:00Z',
                     'view_count':12}],True)
        if endpoint=='/clips':
            return ([{'id':'c1','broadcaster_id':'u1','creator_id':'x',
                     'title':'Clip','language':'es','created_at':'2026-11-05T01:00:00Z',
                     'view_count':7}],True)
        raise AssertionError(endpoint)

class PipelineTest(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalized('Pokémon: Test!'),'pokemon test')
        self.assertEqual(similarity('GAME A','game a'),1.0)

    def test_storage_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=connect(Path(tmp)/'radar.sqlite3')
            save_game(c,{'id':'123','name':'Game'},'test','2026-11-05T00:00:00Z')
            c.execute('INSERT INTO runs(run_id,started_utc,status) VALUES (?,?,?)',
                      ('run1','2026-11-05T00:00:00Z','success'))
            capture='run1:123'
            store_capture(c,FakeAPI(),'123',capture,'2026-11-05T04:17:00Z','run1',100,100,100,8)
            store_capture(c,FakeAPI(),'123',capture,'2026-11-05T04:17:00Z','run1',100,100,100,8)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM stream_snapshots').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM twitch_videos').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM twitch_clips').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM video_observations').fetchone()[0],1)
            out=aggregate(c,dt.date(2026,11,6))
            self.assertEqual(out['alerts'],0)
            export_csv(c,Path(tmp)/'exports')
            self.assertTrue((Path(tmp)/'exports/game_metrics_daily.csv').exists())
            c.close()

    def test_alert_14_complete_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=connect(Path(tmp)/'db.sqlite3')
            # 14 días completos: 7 previos a 1000; 7 recientes a 1700 (+700, +70%).
            asof=dt.date(2026,11,6)
            for dayindex in range(14):
                day=asof-dt.timedelta(days=14-dayindex)
                for hour in range(0,24,4):
                    timestamp=f'{day.isoformat()}T{hour:02d}:17:00Z'
                    cap=f'{dayindex}-{hour}'
                    c.execute('INSERT INTO game_snapshots VALUES (?,?,?,?,?,?,?)',
                        (cap,'g1',timestamp,1000 if dayindex<7 else 1700,10,1,'r'))
            result=aggregate(c,asof)
            self.assertEqual(result['alerts'],1)
            self.assertEqual(result['games_evaluable'],1)
            c.execute("DELETE FROM game_snapshots WHERE capture_id='3-4'")
            self.assertEqual(aggregate(c,asof)['alerts'],0)
            c.close()

if __name__=='__main__':unittest.main()
