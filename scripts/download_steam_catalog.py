"""Descarga el último catálogo histórico válido del repositorio Steam.

Requiere STEAM_GH_TOKEN con permiso Actions:read sobre el repositorio Steam.
No depende del nombre del workflow (radar.yml) porque algunos accesos de
GitHub devuelven 404 al resolver workflows por nombre.

Uso:
    python scripts/download_steam_catalog.py \
        --repo yeifer86intl/geca-radar \
        --out data/geca_steam_historico.xlsx \
        --csv data/steam_maestro_actual.csv
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pandas as pd

ARTIFACT = "geca-steam-catalog"
BOOK_NAME = "geca_steam_historico.xlsx"
REQUIRED = {"appid", "nombre", "lanzamiento", "ficha_verificada_utc"}
COLUMNS = [
    "appid", "nombre", "lanzamiento", "resenas_total",
    "resenas_positivas", "resenas_negativas", "ficha_verificada_utc",
    "coming_soon", "ficha_estado", "ejecucion",
]


def github_command(args, env, token):
    try:
        result = subprocess.run(
            ["gh", *args], text=True, capture_output=True, env=env,
            check=False, timeout=120,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("GitHub CLI (gh) no está instalado.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("GitHub CLI superó el tiempo de espera.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "Sin detalle").strip()
        detail = detail.replace(token, "[TOKEN_OCULTO]")
        raise RuntimeError("Falló GitHub CLI: " + detail)
    return result.stdout


def run(repo: str, out: str, result_csv: str):
    token = os.getenv("STEAM_GH_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Falta STEAM_GH_TOKEN en los Secrets de Actions de geca_twitch."
        )
    env = dict(os.environ)
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token

    # Consultar ejecuciones exitosas sin fijar --workflow radar.yml.
    # La selección final se hace por la presencia del artifact requerido.
    output = github_command(
        ["run", "list", "--repo", repo, "--status", "success",
         "--limit", "100", "--json", "databaseId"], env, token
    )
    try:
        runs = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub no devolvió un listado JSON válido.") from exc
    if not runs:
        raise RuntimeError("No se encontraron ejecuciones exitosas de Steam.")

    target_book = Path(out)
    target_csv = Path(result_csv)
    target_book.parent.mkdir(parents=True, exist_ok=True)
    target_csv.parent.mkdir(parents=True, exist_ok=True)

    # La carpeta temporal evita conservar archivos de descargas fallidas.
    with tempfile.TemporaryDirectory(prefix="geca_steam_") as tmp:
        for item in runs:
            run_id = str(item["databaseId"])
            destination = Path(tmp) / run_id
            destination.mkdir()
            result = subprocess.run(
                ["gh", "run", "download", run_id, "--repo", repo,
                 "-n", ARTIFACT, "-D", str(destination)],
                text=True, capture_output=True, env=env, check=False,
                timeout=240,
            )
            book = destination / BOOK_NAME
            if result.returncode or not book.is_file():
                continue  # Ejecución de otro workflow, o artifact no disponible.

            try:
                sheet = pd.read_excel(
                    book, sheet_name="vigente", dtype={"appid": str}
                )
            except (ValueError, KeyError) as exc:
                raise RuntimeError(
                    f"El artifact de la ejecución {run_id} no tiene una hoja 'vigente' válida."
                ) from exc
            missing = REQUIRED.difference(sheet.columns)
            if missing:
                raise RuntimeError(
                    f"El catálogo Steam carece de columnas: {sorted(missing)}"
                )
            if sheet["appid"].isna().any() or sheet["appid"].duplicated().any():
                raise RuntimeError("El catálogo contiene AppID nulos o repetidos.")

            for col in COLUMNS:
                if col not in sheet.columns:
                    sheet[col] = ""

            # Publicar el libro solamente después de comprobar la estructura.
            shutil.copy2(book, target_book)
            sheet[COLUMNS].to_csv(
                target_csv, sep=";", index=False, encoding="utf-8-sig"
            )
            print(f"Catálogo Steam importado: {len(sheet)} juegos. Run {run_id}.")
            return

    raise RuntimeError(
        f"No se encontró un artifact '{ARTIFACT}' entre las últimas "
        "100 ejecuciones exitosas accesibles de Steam. Comprueba que el "
        "workflow de Steam haya terminado y publicado ese artifact; "
        "revisa también el acceso Actions:read del token."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", default="data/geca_steam_historico.xlsx")
    parser.add_argument("--csv", default="data/steam_maestro_actual.csv")
    args = parser.parse_args()
    run(args.repo, args.out, args.csv)
