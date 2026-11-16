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
    for row in c.execute('SELECT steam_appid,steam_name,release_date,verified_utc,payload_json FROM steam_games'):
        release=parse_release(row['release_date'])
        try: verified=dt.datetime.fromisoformat((row['verified_utc'] or '').replace('Z','+00:00')).date()
        except ValueError: verified=None
        try:
            import json
            record=json.loads(row['payload_json'])
            upcoming=str(record.get('coming_soon','')).strip().lower() in ('true','1','sí','si')
            status=str(record.get('ficha_estado','')).strip().lower()
        except (ValueError,TypeError):
            upcoming=False;status=''
        if not upcoming or status!='ok' or not verified:continue
        age=(asof-verified).days
        if (release is None or release >= asof) and 0 <= age <= 7:
            result.append({'steam_appid':row['steam_appid'],'steam_name':row['steam_name'],
                           'release_date':release.isoformat() if release else row['release_date'],
                           'verified_utc':row['verified_utc'],
                           'days_until_release':(release-asof).days if release else None})
    return sorted(result,key=lambda r:(r['days_until_release'] is None, r['days_until_release'] if r['days_until_release'] is not None else 999999,r['steam_name']))
