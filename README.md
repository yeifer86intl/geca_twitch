# GECA Twitch — Ingesta transaccional + métricas + GitHub Actions

Proyecto alineado con `GECA_RADAR_PRESENTACION_EJECUTIVA`:

- Steam da el catálogo; se resuelve la identidad de Twitch **por nombre**, nunca por igualdad de IDs.
- Se conserva evidencia de Twitch con fecha, identificador y alcance de la consulta.
- Se separa RAW transaccional de agregados y alertas.
- La alerta `suba de atención` exige **14 días UTC completos**, seis franjas de 4 horas sin huecos, streams exhaustivos, promedio previo >0, aumento >=500 espectadores y >=50%.
- El radar propone señales: la decisión editorial pertenece al analista.

## Carpetas

```
.github/workflows/geca-twitch.yml  # programador GitHub Actions cada 4 horas
src/geca_twitch/twitch_api.py       # cliente Twitch Helix y OAuth
src/geca_twitch/db.py               # esquema SQLite
src/geca_twitch/ingest.py           # Steam -> matching -> Twitch -> RAW
src/geca_twitch/aggregate.py        # GOLD, alertas y CSV
scripts/restore_latest_artifact.py  # restaura histórico SQLite de artifacts
 data/steam_maestro_20261103.csv    # catálogo incluido, corte histórico
 tests/test_pipeline.py             # pruebas sin conexión
```

## Instalación en GitHub

1. Copia el contenido de **esta carpeta** a la raíz del repo (conservando `.github/workflows`). No subas el ZIP cerrado como único archivo.
2. En el repo GitHub: **Settings → Secrets and variables → Actions → New repository secret**. Crea `TWITCH_CLIENT_ID` y `TWITCH_CLIENT_SECRET`.
3. **Revoca/regenera el client secret previamente compartido en un chat** antes de usarlo en GitHub.
4. Haz `git add .`, `git commit -m "GECA Twitch transaccional"`, `git push origin main`.
5. En **Actions → GECA Twitch - transaccional cada 4 horas → Run workflow**, ejecuta la primera prueba manual.
6. Abre cada run: revisa logs y descarga artifacts `geca-twitch-state` y `geca-twitch-exports`.

### GitHub Actions

`17 */4 * * *` UTC corresponde a 00:17, 04:17, 08:17, 12:17, 16:17 y 20:17 en Venezuela (UTC-4). GitHub puede retrasar u omitir ejecuciones de cron. **La regla de alerta no inventa observaciones faltantes** y puede quedar inhabilitada si falta una franja.

El job tiene `concurrency` no cancelable y permiso `actions: read`. Descarga la última copia SQLite como artifact y sube el estado actualizado **incluso cuando falla la ingesta**, para evitar perder avances del lote. Los artifacts están configurados con `retention-days: 30`. Eso **NO es almacenamiento garantizado permanente**: para uso productivo migra RAW a PostgreSQL/objeto persistente y define una política de retención, backups y eliminación conforme a permisos Twitch.

### Control de volumen y cobertura

El archivo YAML define:
- `--tracked-limit 120`: cohorte estable priorizada para acumular seis capturas diarias. **No equivale al catálogo completo**; actualmente se escoge por ID de Twitch.
- `--rotation-limit 30`: hasta 30 categorías adicionales rotan cada ejecución para exploración, pero **generalmente no cumplen las 14 jornadas completas**.
- `--fuzzy-limit 20`: búsquedas aproximadas pendientes por ejecución; los matches exactos del catálogo se consultan por lotes de hasta 100.
- `--max-streams 300`, `--max-videos 100`, `--max-clips 100`: topes por juego y ejecución; pueden impedir cobertura completa. En particular, `streams_complete=0` invalida ese día para alertas.
- `--discover-limit 100`: categorías top de Twitch, conservadas aunque no coincidan con Steam.

Los datos de clips consultan una ventana solapada de 8 horas. Como Twitch ordena clips principalmente por vistas y tiene límites de paginación, **esto no garantiza encontrar todos los clips**. Los videos de un juego también tienen límites de recuperación en Twitch. No se deben usar los conteos para afirmar cobertura exhaustiva.

El catálogo de Steam incluido está fechado `2026-11-03`. **Actualiza ese CSV** en `data/` o modifica el argumento `--steam` para apuntar al `maestro.csv` nuevo producido por tu pipeline Steam. Esta integración no descarga por sí misma los artifacts de Steam de otro workflow.

## Ejecución local (PowerShell de Windows)

Desde la raíz del proyecto:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:TWITCH_CLIENT_ID="TU_NUEVO_CLIENT_ID"
$env:TWITCH_CLIENT_SECRET="TU_NUEVO_CLIENT_SECRET"
$env:PYTHONPATH="src"
python -m geca_twitch.ingest --steam data/steam_maestro_20261103.csv --db state/geca_twitch.sqlite3
python -m geca_twitch.aggregate --db state/geca_twitch.sqlite3 --out outputs
```

GitHub Actions usa Linux, no requiere activar entornos virtuales ni definir PYTHONPATH manualmente porque el YAML ya lo hace.

Para probar sin red:

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```

## Tablas RAW

- `steam_games`: campos originales Steam + contadores de reseñas de **Steam**.
- `twitch_games`: categorías encontradas en Helix.
- `game_matches`: asociación Steam–Twitch y estado `matched|ambiguous|unmatched`.
- `match_candidates`: candidatos de búsqueda aproximada con puntuación.
- `stream_snapshots`: fila por stream/captura, con `viewer_count` y JSON de origen.
- `game_snapshots`: total de espectadores observados por juego/captura y flag de exhaustividad.
- `twitch_videos`, `video_observations`: metadatos únicos y mediciones periódicas de vistas.
- `twitch_clips`, `clip_observations`: metadatos únicos y mediciones periódicas de vistas.
- `runs`: auditoría, errores, fecha y conteos por ejecución.

Los datos de espectadores son **instantáneos de transmisiones recuperadas**, NO espectadores únicos ni tiempo total visto.

## Salidas CSV

`outputs/steam_twitch_matches.csv`, `twitch_only.csv`, `game_metrics_daily.csv`, `attention_alerts.csv`, `steam_reception.csv`, `runs.csv`.

`steam_reception.csv` solo contiene los recuentos positivos/negativos que ya vienen del CSV de Steam. **No hay acceso general al historial de chat de Twitch mediante Helix**; no se presenta sentimiento de Twitch porque todavía no existen mensajes capturados. Como futura extensión, usar EventSub con autorización, servidor persistente y cumplimiento de reglas Twitch para mensajes de chat. Tampoco se usa sentimiento de títulos de streams como sustituto de opiniones de jugadores.

## Limitaciones de esta versión

- SQLite se transporta por artifacts: uso piloto, no despliegue definitivo de base transaccional.
- Los límites por juego y la rotación hacen que haya cobertura parcial. Un día sin las seis franjas o un corte en streams invalida la alerta.
- Para cientos/miles de juegos activos y 6 corridas diarias, conviene PostgreSQL, colas y almacenamiento de objetos; medir cuota y duración del workflow.
- Matching aproximado con puntuación >=0.95 y margen de 0.10 es heurístico. Para franquicias/remasters, revisa `ambiguous` antes de usar el juego en reglas editoriales.
- La regla de `Juego a lanzarse` del PDF (Steam próximos lanzamientos y ficha verificada últimos 7 días) se conserva conceptualmente en el catálogo, pero **esta versión de Twitch no genera todavía propuestas de lanzamiento** ni la cola humana; debe integrarse con el pipeline Steam existente.
- No se ha ejecutado una descarga real de Twitch desde este entorno; es necesaria una prueba de Actions con credenciales válidas.
