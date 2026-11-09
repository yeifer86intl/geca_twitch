# GECA Radar · Twitch transaccional v2

Proyecto de ingesta y métricas conforme al PDF **GECA / MediaPro – Radar / La decisión (19 oct 2026)**.

**Sin descarga de videos, sin transcripción de audio, sin LLM y sin análisis de comentarios de Twitch.** Se almacenan las URL públicas que la API devuelva, como referencias para posibles análisis autorizados futuros. El indicador de reseñas de Steam no es sentimiento de Twitch.

## Arquitectura y metodología

1. **Identificar:** leer catálogo Steam; buscar coincidencias exactas en Helix y sugerir candidatos de similitud para revisión; almacenar categorías principales de Twitch incluso si no aparecen en Steam. IGDB ID se conserva cuando Twitch lo devuelve, pero NO se equipara automáticamente a un AppID de Steam.
2. **Evidencia:** guardar snapshots por transmisión, por juego, videos, clips, observaciones de visualizaciones, timestamps y JSON fuente; conservar URLs del contenido sin descargarlo.
3. **Reglas del PDF:** (a) Steam: próximo lanzamiento con ficha verificada dentro de 7 días; (b) Twitch: 14 días UTC completos, 6 franjas por día, captura de streams completa y audiencia previa >0; diferencia >=500 espectadores **y** >=50%.
4. **Resumen:** generar de 0 a 3 propuestas, priorizando señales Twitch y después lanzamientos Steam; este orden de desempate es una decisión técnica configurable, no un requisito del PDF.
5. **Analista:** registrar manualmente `analizar`, `seguir`, `descartar`, `posponer` con fecha, nota y responsable.

## Base de datos

**16 tablas**: 13 originales + `attention_evaluations`, `editorial_proposals`, `editorial_decisions`.

Las tablas originales son `steam_games`, `twitch_games`, `game_matches`, `match_candidates`, `runs`, `stream_snapshots`, `game_snapshots`, `twitch_videos`, `video_observations`, `twitch_clips`, `clip_observations`, `daily_metrics` y `attention_alerts`.

Las nuevas agregan: auditoría para juegos no evaluables (con motivo), propuestas justificadas y registro humano de decisiones. Las tablas existentes reciben migraciones **aditivas** al abrir la base:

- `twitch_games`: `igdb_id`.
- `stream_snapshots`: `user_login`, `tags_json`, `thumbnail_url`, `is_mature`.
- `twitch_videos`: `user_name`, `stream_id`, `thumbnail_url`, `viewable`.
- `twitch_clips`: `creator_name`, `broadcaster_name`, `video_id`, `vod_offset`, `is_featured`, `thumbnail_url`.

Se conservan `payload_json`, URL de videos, URL de clips y mediciones posteriores. **Sin análisis multimedia.**

## Requisitos y configuración local

En PowerShell con Anaconda:

```powershell
conda create -n geca-twitch python=3.12 -y
conda activate geca-twitch
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edita `.env` e introduce tus credenciales de Twitch **renovadas**. Nunca subas `.env` al repositorio (ya está en `.gitignore`). Este proyecto carga `.env` automáticamente en la ingesta local; GitHub Actions inyecta Secrets sin crear `.env`.

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
python -m geca_twitch.ingest --steam data/steam_maestro_20261103.csv --db state/geca_twitch.sqlite3
python -m geca_twitch.aggregate --db state/geca_twitch.sqlite3 --out outputs
```

No necesitas credenciales para volver a calcular agregados ni para usar `notebooks/explorar_geca_twitch.ipynb`.

**Importante:** el catálogo incluido `steam_maestro_20261103.csv` es un corte histórico. Para detectar lanzamientos nuevos debes actualizarlo con las salidas de Steam y asegurarte de que `ficha_verificada_utc` refleje una verificación real de los últimos siete días. El pipeline no recalcula por sí solo las fechas de verificación de Steam. Valores sin fecha exacta no activan esa regla.

### Decisión editorial manual

Consultar `outputs/editorial_proposals.csv`, tomar su `proposal_day` y `game_key`, y ejecutar:

```powershell
python -m geca_twitch.decide --day 2026-11-09 --game-key steam:123456 --decision seguir --analyst analista --note 'Revisar ficha actualizada'
python -m geca_twitch.aggregate --db state/geca_twitch.sqlite3 --out outputs
```

El ejemplo usa un ID ficticio. `decide` solo permite actuar sobre una propuesta existente. La decisión manual se guarda en el SQLite, **no se sincroniza automáticamente desde tu PC a GitHub**. Si deseas que sea persistente en producción, registra decisiones sobre la base de referencia mediante una interfaz o servicio de escritura autorizado.

## GitHub Actions cada cuatro horas

Workflow: `.github/workflows/geca-twitch.yml`. Programa `17 */4 * * *` (UTC), seis veces diarias. Ejecuta pruebas, restaura la SQLite más reciente de artifacts, ingesta, agregados y publica artifacts `geca-twitch-state` y `geca-twitch-exports`. GitHub puede demorar/omitir algunos disparos del cron: no se promete cobertura de 6/6.

En GitHub: **Settings → Secrets and variables → Actions**, definir `TWITCH_CLIENT_ID` y `TWITCH_CLIENT_SECRET`. Usa un secreto nuevo: el antiguo fue compartido en la conversación. No es necesario subir `.env`.

El repositorio debe contener `.github/workflows/geca-twitch.yml` y ejecutarse desde su raíz. Tras hacer push a `main`, prueba desde Actions → Run workflow.

**Respaldo:** un artifact con retención de 30 días no es una base de datos gestionada ni una garantía de persistencia indefinida. Si el artifact caduca o se pierde, el pipeline puede iniciar con una base vacía; para producción necesitarás PostgreSQL o almacenamiento persistente + copias de seguridad. No abras a escritura el mismo SQLite en varios procesos simultáneos.

## Archivos exportados

- `steam_twitch_matches.csv`: claves y matching, con IGDB ID cuando exista.
- `twitch_only.csv`: categorías Twitch sin `matched` confirmado; **no** prueba que no existan en Steam.
- `game_metrics_daily.csv`: 6 métricas + cobertura y contadores diarios.
- `attention_evaluations.csv`: incluye `reason` para explicar por qué no es evaluable.
- `attention_alerts.csv`: solo juegos que cumplen la regla.
- `editorial_proposals.csv`, `editorial_decisions.csv`: cola y trazabilidad editorial.
- `twitch_video_links.csv`, `twitch_clip_links.csv`: URLs y metadatos **sin archivos multimedia**.
- `steam_reception.csv`: reseñas de Steam, separadas del sentimiento Twitch.
- `runs.csv`: auditoría de ingestas.

## Alcances y limitaciones

- 120 juegos se mantienen como cohorte estable y 30 rotan por defecto. Esos 30 no tendrán normalmente 14 días completos para activar alertas.
- Los contadores de videos/clips se recuperan sujetos a límites y paginación; las cantidades no son un censo total de Twitch.
- `avg_viewers` es un promedio de **snapshots observados**, no audiencia única, ni horas vistas exactas.
- La regla de cobertura exige seis franjas distintas UTC por cada uno de 14 días. Un retraso de GitHub puede hacer un día no evaluable. La validez del alcance depende además de mantener los mismos límites de extracción y el mismo conjunto de juegos.
- `attention_evaluations.reason` permite auditar `missing_day`, `incomplete_streams`, `missing_or_duplicate_slot`, `zero_previous_average`, `threshold_not_met`.
- `editorial_proposals` genera hasta tres propuestas por ejecución, no rankings de popularidad. El estado `pending` se recalcula, pero decisiones previas se conservan.
- El proyecto **no** descarga videos, no transcribe, no ejecuta LLM, no procesa mensajes de chat y no crea porcentajes positivos/negativos de Twitch.

Documentación: https://dev.twitch.tv/docs/api/reference/ y https://docs.github.com/en/actions


## v2.1: cobertura completa del maestro (sin rotación)

**Objetivo:** cada ejecución intenta obtener una medición de todos los juegos
con correspondencia **confirmada** en Twitch del maestro Steam completo.
No hay `tracked-limit=120`, ni `rotation-limit=30`, ni `fuzzy-limit=20`.
Los nombres pendientes se buscan sin recorte artificial durante la primera ingesta.
Los no encontrados y ambiguos se conservan en la base, y **no se inventa** un ID Twitch.

El archivo `outputs/steam_catalog_coverage.csv` incluye **una fila por cada juego Steam**,
con `match_status`, `monitoring_status`, fecha de última captura y número de capturas.
Comparar sus filas contra `steam_games` antes de dar por concluida una ejecución.

El workflow utiliza `scripts/select_steam_master.py`. Prioridad:
1. `STEAM_CATALOG` si se suministra mediante variable de entorno.
2. `data/maestro.csv` si existe.
3. El archivo `data/steam_maestro_*.csv` de mayor fecha en su nombre.

Esto **no descarga** automáticamente un maestro generado en otro repositorio o workflow.
Para datos recientes, copiar el maestro actualizado a `data/maestro.csv` antes de ejecutar
GitHub Actions o crear una integración explícita con su fuente.

Por defecto los streams y clips **no** tienen máximo local de filas y se recorren sus páginas;
los videos mantienen el límite de Twitch (~500 resultados por juego mediante `/videos?game_id`).
`--discover-limit 0` desactiva solo el descubrimiento **extra** de categorías populares,
no la búsqueda de juegos del maestro. El crecimiento del catálogo puede hacer que la
extracción total supere las cuatro horas: GitHub puede retrasar o serializar trabajos.
El timeout del job se elevó a 330 minutos; NO garantiza completar toda la lista en
una sola ejecución. Revisar `runs.csv`, `steam_catalog_coverage.csv` y logs. Los juegos
con coincidencia ambigua o no encontrados requieren resolución de identidad.

No se descargan ni se transcriben videos. Se guardan URLs, metadatos y vistas
observadas; no se infiere el sentimiento de Twitch.

### Ejecución local en PowerShell (Anaconda)

```powershell
conda activate geca-twitch
$env:PYTHONPATH = 'src'
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
$steam = python scripts/select_steam_master.py
python -m geca_twitch.ingest --steam $steam --db state/geca_twitch.sqlite3 --discover-limit 0 --max-videos 500
python -m geca_twitch.aggregate --db state/geca_twitch.sqlite3 --out outputs
```
