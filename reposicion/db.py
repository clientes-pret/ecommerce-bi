#!/usr/bin/env python3
"""
reposicion/db.py — helpers finos sobre la REST de Supabase (PostgREST).

Mismo patrón que semaforo_ml.py:subir_a_supabase() (headers con
service_role_key, POST directo con `requests`) — sin librerías nuevas.

Config: variable de entorno CONFIG_JSON (JSON completo, usado por los jobs
en GitHub Actions) o, si no existe, config.json local (uso manual/testing).
"""

import json
import os
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent


def load_config():
    raw = os.environ.get("CONFIG_JSON")
    if raw:
        return json.loads(raw)
    config_path = ROOT / "config.json"
    with open(config_path) as f:
        return json.load(f)


def _sb(config):
    sb = config.get("supabase")
    if not sb or not sb.get("url") or not sb.get("service_role_key"):
        raise RuntimeError("Falta config['supabase'] (url + service_role_key)")
    return sb


def _headers(config, prefer=None):
    sb = _sb(config)
    headers = {
        "apikey": sb["service_role_key"],
        "Authorization": f"Bearer {sb['service_role_key']}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _url(config, table):
    return f"{_sb(config)['url']}/rest/v1/{table}"


def select(config, table, params=None):
    """GET con filtros PostgREST (ej: {'sku': 'eq.ABC123', 'select': 'sku,nombre'})."""
    r = requests.get(_url(config, table), headers=_headers(config), params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def insert(config, table, rows, ignore_duplicates=False, chunk=500):
    """POST en lotes de `chunk`. `ignore_duplicates=True` => no falla si viola
    un unique constraint (usado para upserts "bare" o inserts idempotentes,
    ej. detección de quiebre con el índice único parcial)."""
    if not rows:
        return
    prefer = "resolution=ignore-duplicates,return=minimal" if ignore_duplicates else "return=minimal"
    for i in range(0, len(rows), chunk):
        lote = rows[i:i + chunk]
        r = requests.post(_url(config, table), headers=_headers(config, prefer), json=lote, timeout=30)
        if r.status_code not in (200, 201, 409):
            raise RuntimeError(f"Insert falló en {table}: {r.status_code} {r.text[:300]}")


def upsert(config, table, rows, on_conflict, chunk=500):
    """POST con Prefer: resolution=merge-duplicates — upsert real (pisa las
    columnas enviadas si ya existe la fila, según `on_conflict`)."""
    if not rows:
        return
    headers = _headers(config, prefer="resolution=merge-duplicates,return=minimal")
    for i in range(0, len(rows), chunk):
        lote = rows[i:i + chunk]
        r = requests.post(
            _url(config, table), headers=headers, json=lote,
            params={"on_conflict": on_conflict}, timeout=30,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Upsert falló en {table}: {r.status_code} {r.text[:300]}")


def patch(config, table, params, body):
    """PATCH con filtros PostgREST (ej: {'id': 'eq.42'})."""
    r = requests.patch(_url(config, table), headers=_headers(config, prefer="return=minimal"),
                        params=params, json=body, timeout=30)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Patch falló en {table}: {r.status_code} {r.text[:300]}")
