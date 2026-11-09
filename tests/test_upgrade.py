"""Pruebas de migración, metadatos, regla Steam y auditoría de cobertura."""
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from geca_twitch.db import connect
from geca_twitch.ingest import save_game, store_capture
from geca_twitch.aggregate import aggregate, export_csv
from geca_twitch.releases import parse_release, get_launch_candidates

class MockAPI:
    def pages(self,endpoint,params,max_rows):
        items={
           '/streams':[{'id':'s','user_id':'u','user_login':'user','user_name':'User',
             'tags':['English','Ranked'],'is_mature':False,'thumbnail_url':'https://example.org/stream',
             'viewer_count':123,'title':'stream','language':'es','started_at':'2026-11-07T00:00:00Z'}],
           '/videos':[{'id':'v','user_id':'u','user_name':'User','stream_id':'s','viewable':'public',
             'thumbnail_url':'https://example.org/vid','url':'https://www.twitch.tv/videos/123',
             'title':'video','created_at':'2026-11-07T00:00:00Z','view_count':77}],
           '/clips':[{'id':'c','creator_name':'Creator','broadcaster_name':'User',
             'video_id':'v','vod_offset':45,'is_featured':True,'view_count':20,
             'url':'https://clips.twitch.tv/test','created_at':'2026-11-07T00:00:00Z'}],
        }
        return items[endpoint],True

class UpgradeTest(unittest.TestCase):
    def test_release_dates(self):
        self.assertEqual(parse_release('3 NOV 2026'),dt.date(2026,11,3))
        self.assertEqual(parse_release('2027-12-31'),dt.date(2027,12,31))
        self.assertIsNone(parse_release('Coming soon'))

    def test_fields_and_links_no_media_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=connect(Path(tmp)/'db.sqlite3')
            save_game(c,{'id':'g','name':'Demo','igdb_id':'999'},'top','2026-11-07T00:00:00Z')
            self.assertEqual(c.execute('SELECT igdb_id FROM twitch_games').fetchone()[0],'999')
            store_capture(c,MockAPI(),'g','run:g','2026-11-07T04:17:00Z','run',100,100,100,8)
            self.assertEqual(c.execute('SELECT user_login FROM stream_snapshots').fetchone()[0],'user')
            self.assertEqual(json.loads(c.execute('SELECT tags_json FROM stream_snapshots').fetchone()[0]),['English','Ranked'])
            self.assertEqual(c.execute('SELECT user_name FROM twitch_videos').fetchone()[0],'User')
            self.assertEqual(c.execute('SELECT is_featured FROM twitch_clips').fetchone()[0],1)
            export_csv(c,Path(tmp)/'out')
            self.assertTrue((Path(tmp)/'out/twitch_video_links.csv').exists())
            self.assertTrue((Path(tmp)/'out/twitch_clip_links.csv').exists())
            c.close()

    def test_launch_and_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=connect(Path(tmp)/'db.sqlite3')
            c.execute('''INSERT INTO steam_games VALUES (?,?,?,?,?,?,?,?,?)''',
                      ('id1','Futuro','12 NOV 2026',0,0,0,'2026-11-07T12:00:00Z','{}','2026-11-09T12:00:00Z'))
            self.assertEqual(len(get_launch_candidates(c,dt.date(2026,11,9))),1)
            result=aggregate(c,dt.date(2026,11,9))
            self.assertEqual(result['proposals'],1)
            self.assertEqual(c.execute('SELECT game_key FROM editorial_proposals').fetchone()[0],'steam:id1')
            c.close()

    def test_evaluation_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=connect(Path(tmp)/'db.sqlite3')
            c.execute('INSERT INTO game_snapshots VALUES (?,?,?,?,?,?,?)',
                      ('c','g','2026-11-08T00:17:00Z',200,1,1,'r'))
            out=aggregate(c,dt.date(2026,11,9))
            self.assertEqual(out['alerts'],0)
            self.assertEqual(c.execute('SELECT reason FROM attention_evaluations').fetchone()[0],'missing_day')
            c.close()

if __name__=='__main__':unittest.main()
