import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from geca_twitch.db import connect
from geca_twitch.releases import get_launch_candidates
from geca_twitch.aggregate import aggregate

class IntegrationTest(unittest.TestCase):
 def test_published_not_eligible(self):
  with tempfile.TemporaryDirectory() as temp:
   c=connect(Path(temp)/'test.db')
   for k,coming in [('future',True),('published',False)]:
    c.execute('INSERT INTO steam_games VALUES (?,?,?,?,?,?,?,?,?)',
      (k,k,'2026-12-01',0,0,0,'2026-11-11T00:00:00Z',
       json.dumps({'coming_soon':coming,'ficha_estado':'ok'}),'2026-11-12T00:00:00Z'))
   r=get_launch_candidates(c,dt.date(2026,11,12))
   self.assertEqual([x['steam_appid'] for x in r],['future'])
   output=aggregate(c,dt.date(2026,11,12))
   self.assertEqual(output['proposals'],1)
   self.assertEqual(c.execute('SELECT COUNT(*) FROM geca_signals').fetchone()[0],1)
   c.close()

if __name__=='__main__':unittest.main()
