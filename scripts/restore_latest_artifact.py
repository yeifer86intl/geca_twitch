"""Descarga el último artifact SQLite NO caducado de este repo (REST GitHub)."""
import io
import os
import pathlib
import sys
import zipfile
import requests

def main():
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    repo=os.environ.get('GITHUB_REPOSITORY')
    if not repo or not token:
        print('Sin contexto GitHub; primera ejecución local.');return
    headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
             'X-GitHub-Api-Version':'2022-11-28'}
    url=f'https://api.github.com/repos/{repo}/actions/artifacts'
    response=requests.get(url,params={'name':'geca-twitch-state','per_page':100},headers=headers,timeout=30)
    response.raise_for_status()
    artifacts=[a for a in response.json().get('artifacts',[]) if not a.get('expired') and a['name']=='geca-twitch-state']
    if not artifacts:
        print('No existe artifact previo; base nueva.');return
    # Ordenar por ID evita seleccionar estados antiguos aunque timestamps puedan empatar.
    newest=max(artifacts,key=lambda a:int(a['id']))
    res=requests.get(newest['archive_download_url'],headers=headers,timeout=90)
    res.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        files=[n for n in z.namelist() if pathlib.PurePosixPath(n).name=='geca_twitch.sqlite3']
        if len(files)!=1:raise ValueError('Artifact inesperado: requiere una SQLite')
        path=pathlib.Path('state/geca_twitch.sqlite3');path.parent.mkdir(exist_ok=True)
        path.write_bytes(z.read(files[0]))
    # Verificar antes de utilizar un artifact recuperado.
    import sqlite3
    c=sqlite3.connect('state/geca_twitch.sqlite3')
    check=c.execute('PRAGMA integrity_check').fetchone()[0];c.close()
    if check!='ok':raise RuntimeError('SQLite del artifact no supera integrity_check')
    print('Estado restaurado desde artifact ID:',newest['id'])

if __name__=='__main__':main()
