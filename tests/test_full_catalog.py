"""Asegura cobertura completa del maestro en lugar de rotaciones."""
import tempfile
import unittest
from pathlib import Path
from geca_twitch.db import connect
from geca_twitch.ingest import choose_games, match_catalog, save_game
from geca_twitch.aggregate import export_csv

class CatalogTest(unittest.TestCase):
    def test_all_matched_not_only_150(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=connect(Path(tmp)/'db.sqlite3')
            for i in range(231):
                steam=str(i)
                gid='tw'+str(i)
                c.execute('INSERT INTO steam_games VALUES (?,?,?,?,?,?,?,?,?)',
                          (steam,'Game '+steam,None,None,None,None,None,'{}','2026-11-09'))
                save_game(c,{'id':gid,'name':'Game '+steam},'test','2026-11-09')
                c.execute('INSERT INTO game_matches VALUES (?,?,?,?,?,?)',
                          (steam,gid,1.0,'matched','exact','2026-11-09'))
            c.commit()
            ids,total,stable=choose_games(c,120,30,1)
            self.assertEqual(len(ids),231)
            self.assertEqual(total,231)
            export_csv(c,Path(tmp)/'out')
            import csv
            with open(Path(tmp)/'out/steam_catalog_coverage.csv',encoding='utf-8-sig') as f:
                self.assertEqual(len(list(csv.DictReader(f))),231)
            c.close()

    def test_unmatched_reported_without_false_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=connect(Path(tmp)/'db.sqlite3')
            c.execute('INSERT INTO steam_games VALUES (?,?,?,?,?,?,?,?,?)',
                      ('1','Unknown Game',None,None,None,None,None,'{}','2026-11-09'))
            c.commit()
            self.assertEqual(choose_games(c)[0],[])
            export_csv(c,Path(tmp)/'out')
            self.assertIn('pending_identity_search',
                          (Path(tmp)/'out/steam_catalog_coverage.csv').read_text(encoding='utf-8-sig'))
            c.close()

if __name__=='__main__': unittest.main()
