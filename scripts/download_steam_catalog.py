"""Descarga el libro de Steam desde el repositorio ge ca-radar vía gh.
Usar token de acceso de lectura Actions del repositorio de origen.
"""
import argparse, json, os, pathlib, subprocess, sys
import pandas as pd

def run(repo, out, result_csv):
    token=os.environ.get('STEAM_GH_TOKEN','')
    if not token: raise RuntimeError('Configura STEAM_GH_TOKEN con acceso Actions:read al repositorio Steam')
    environment={**os.environ,'GH_TOKEN':token}
    q=subprocess.run(['gh','run','list','--repo',repo,'--workflow','radar.yml',
       '--status','success','--limit','20','--json','databaseId'],
       capture_output=True,text=True,env=environment,check=True)
    dest=pathlib.Path(out);dest.parent.mkdir(parents=True,exist_ok=True)
    for item in json.loads(q.stdout):
        target=dest.parent/'downloaded_steam'
        target.mkdir(exist_ok=True)
        for file in target.iterdir():
            if file.is_file():file.unlink()
        result=subprocess.run(['gh','run','download',str(item['databaseId']),
            '--repo',repo,'-n','geca-steam-catalog','-D',str(target)],
            env=environment,capture_output=True,text=True)
        source=target/'geca_steam_historico.xlsx'
        if result.returncode!=0 or not source.is_file():continue
        sheet=pd.read_excel(source,sheet_name='vigente',dtype={'appid':str})
        necessary={'appid','nombre','lanzamiento','ficha_verificada_utc'}
        if not necessary.issubset(sheet.columns):raise ValueError('Libro Steam sin columnas obligatorias')
        if sheet['appid'].duplicated().any():raise ValueError('AppID duplicados en vigente')
        # Conservar todos los AppID históricos; la elegibilidad de lanzamiento se evalúa aparte.
        keep=['appid','nombre','lanzamiento','resenas_total','resenas_positivas',
              'resenas_negativas','ficha_verificada_utc','coming_soon','ficha_estado','ejecucion']
        for c in keep:
            if c not in sheet:sheet[c]=''
        dest.write_bytes(source.read_bytes())
        pathlib.Path(result_csv).parent.mkdir(parents=True,exist_ok=True)
        sheet[keep].to_csv(result_csv,sep=';',index=False,encoding='utf-8-sig')
        print('Catálogo importado:',len(sheet),'AppID. Run:',item['databaseId'])
        return
    raise RuntimeError('No se encontró el artifact geca-steam-catalog válido: no usar un CSV antiguo')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True)
    p.add_argument('--out',default='data/geca_steam_historico.xlsx')
    p.add_argument('--csv',default='data/steam_maestro_actual.csv')
    a=p.parse_args();run(a.repo,a.out,a.csv)
