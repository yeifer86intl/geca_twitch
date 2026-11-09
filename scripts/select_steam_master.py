"""Selecciona maestro.csv si existe, de lo contrario el steam_maestro_* mas reciente."""
import os
from pathlib import Path

explicit=os.environ.get('STEAM_CATALOG')
if explicit:
    p=Path(explicit)
    if not p.is_file():
        raise SystemExit(f'STEAM_CATALOG no existe: {p}')
else:
    preferred=Path('data/maestro.csv')
    if preferred.is_file():
        p=preferred
    else:
        candidates=list(Path('data').glob('steam_maestro_*.csv'))
        if not candidates:
            raise SystemExit('Falta data/maestro.csv o data/steam_maestro_*.csv')
        p=max(candidates,key=lambda x:x.name)
print(p.as_posix())
