"""Regla Steam del PDF: juego próximo a salir y ficha verificada en últimos 7 días."""
import datetime as dt
import re

MONTHS = {'ENE':1,'ENERO':1,'FEB':2,'FEBRERO':2,'MAR':3,'MARZO':3,'ABR':4,'ABRIL':4,
          'MAY':5,'MAYO':5,'JUN':6,'JUNIO':6,'JUL':7,'JULIO':7,'AGO':8,'AGOSTO':8,
          'SEP':9,'SEPT':9,'SEPTIEMBRE':9,'SET':9,'SETIEMBRE':9,'OCT':10,'OCTUBRE':10,
          'NOV':11,'NOVIEMBRE':11,'DIC':12,'DICIEMBRE':12,
          'JAN':1,'APR':4,'AUG':8,'DEC':12}

def parse_release(raw):
    if not raw:return None
    v=str(raw).strip().upper().replace('.', '')
    try:return dt.date.fromisoformat(v[:10])
    except ValueError:pass
    match=re.fullmatch(r'(\d{1,2})\s+([A-ZÁÉÍÓÚÑ]+)\s+(\d{4})',v)
    if match:
        day,month,year=match.groups()
        if month in MONTHS:
            try:return dt.date(int(year),MONTHS[month],int(day))
            except ValueError:return None
    return None

def get_launch_candidates(c,asof):
    result=[]
    for row in c.execute('SELECT steam_appid,steam_name,release_date,verified_utc FROM steam_games'):
        release=parse_release(row['release_date'])
        try: verified=dt.datetime.fromisoformat((row['verified_utc'] or '').replace('Z','+00:00')).date()
        except ValueError: verified=None
        if not release or not verified:continue
        age=(asof-verified).days
        if release >= asof and 0 <= age <= 7:
            result.append({'steam_appid':row['steam_appid'],'steam_name':row['steam_name'],
                           'release_date':release.isoformat(),'verified_utc':row['verified_utc'],
                           'days_until_release':(release-asof).days})
    return sorted(result,key=lambda r:(r['days_until_release'],r['steam_name']))
