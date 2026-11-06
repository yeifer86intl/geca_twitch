#!/usr/bin/env bash
set -euo pipefail
mkdir -p state
# Busca la ultima ejecucion satisfactoria con un artifact twitch-state descargable.
# Si no existe, se crea una base nueva y queda guardada al finalizar.
while IFS= read -r run_id; do
  [[ -z "$run_id" ]] && continue
  echo "Intentando recuperar estado del run $run_id"
  if gh run download "$run_id" --name twitch-state --dir state --repo "$GITHUB_REPOSITORY"; then
    if [[ -s state/geca_twitch.sqlite ]] && python - <<'PY'
import sqlite3
con = sqlite3.connect('state/geca_twitch.sqlite')
valid = con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
con.close()
raise SystemExit(0 if valid else 1)
PY
    then
      echo 'Historial SQLite recuperado y verificado.'
      exit 0
    fi
    rm -f state/geca_twitch.sqlite
  fi
done < <(gh run list --workflow geca-twitch.yml --branch "$GITHUB_REF_NAME" --status success --limit 30 --json databaseId --jq '.[].databaseId' --repo "$GITHUB_REPOSITORY")
echo 'No hay estado previo disponible. Se inicializara una base nueva.'
