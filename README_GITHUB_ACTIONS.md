# GECA Radar — Twitch cada cuatro horas

## Activacion

1. Copia los archivos de este paquete **a la raiz del repositorio `geca-radar`**, incluyendo `.github/workflows/geca-twitch.yml`.
2. En GitHub: **Settings > Secrets and variables > Actions > New repository secret** crea `TWITCH_CLIENT_ID` y `TWITCH_CLIENT_SECRET`. No subas los valores reales al repositorio. Puesto que el secreto se compartio en chat, revocalo y genera uno nuevo.
3. Haz `git add .github/workflows/geca-twitch.yml scripts/restore_state.sh twitch_geca.py requirements.txt steam_maestro_20261103.csv README_GITHUB_ACTIONS.md .gitignore` y luego commit/push a la rama por defecto (`main`).
4. GitHub: **Actions > GECA Twitch - cada 4 horas > Run workflow** para probar manualmente.
5. Descarga los artifacts `twitch-resultados` (CSV) y `twitch-state` (SQLite) desde cada ejecucion.

## Frecuencia

Cron `17 */4 * * *` en UTC (00:17, 04:17, 08:17, 12:17, 16:17, 20:17). Equivale a 20:17, 00:17, 04:17, 08:17, 12:17, 16:17 en Venezuela (UTC-4). Son seis ejecuciones al dia, salvo retrasos o ejecuciones omitidas por GitHub. Se elige el minuto 17 para evitar la congestion de la hora exacta.

## Historial y reglas

- Un runner nuevo no tiene la base SQLite anterior. `scripts/restore_state.sh` descarga el artifact `twitch-state` de la ultima ejecucion correcta disponible; luego se vuelve a subir con la informacion acumulada.
- Se ejecuta el matching Steam/Twitch completo en la ejecucion de las 00 UTC (20:17 Caracas), y en la primera ejecucion sin estado; las restantes reutilizan matches existentes. Para incorporar nuevos registros de Steam, **actualiza `steam_maestro_20261103.csv` con el maestro vigente**; el script de este paquete no descarga automáticamente el maestro del workflow Steam.
- La muestra predeterminada es **hasta 150 categorias por ejecucion**, con **hasta 500 streams por categoria**. Una categoria limitada puede tener `is_complete=0` y no entra en alertas. Para cobertura total, aumenta limites atendiendo a la cuota y el tiempo de ejecucion.
- Alerta de atencion del PDF: 14 dias calendario sin huecos, cobertura comparable, media semana previa > 0, crecimiento medio >= 500 espectadores y >= 50 %. **No se puede disparar una alerta valida antes de reunir el historial**. La API entrega snapshots en vivo, no una serie historica de 14 dias.
- El sentimiento de titulos es exploratorio. Para medir opiniones positivas/negativas de jugadores se debe incorporar chat autorizado en tiempo real; este workflow no hace esa captura.

## Limitaciones

Los artifacts expiran tras 30 dias y no sustituyen una base de datos externa. Si no se ejecuta el workflow durante ese plazo, o todos los artifacts se borran, se perdera la continuidad al reanudar. Para funcionamiento en produccion se recomienda PostgreSQL administrado o almacenamiento persistente tipo objeto. El script descarga el ultimo estado de una ejecucion exitosa; si el extractor termina en estado exitoso con errores parciales, puede haber huecos en las series. Revisa el log `errors` y la bandera `is_complete`.

GitHub puede atrasar u omitir eventos programados. Mantener un repositorio activo (en repositorios publicos, las tareas programadas pueden deshabilitarse tras 60 dias de inactividad).
