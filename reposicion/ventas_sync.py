#!/usr/bin/env python3
"""
reposicion/ventas_sync.py — sincronización incremental de órdenes de venta a
repo_ventas_items, para que reposicion/jobs/weekly_calc.py deje de re-pedir
60 días completos de órdenes en vivo en cada corrida (el cuello de botella
real: ~22000 órdenes de ML Pret cada vez).

No reemplaza la lógica de negocio de core.py — solo decide QUÉ ventana de
tiempo pedirle a cada API (incremental por `updated_at`/`date_last_updated`
en vez de la ventana fija de 60 días) y persiste el resultado aplanado
(core.tn_orders_to_lines/ml_orders_to_lines) en la tabla.
"""

import time
from datetime import datetime, timedelta, timezone

import requests

from reposicion import core, db

CANAL_TIPO = {"tn_pret": "tn", "tn_lavan": "tn", "ml_pret": "ml", "ml_lavan": "ml"}


def _tn_orders_updated_since(cfg, since_iso):
    """Órdenes de TN actualizadas desde `since_iso` — paginado simple, sin
    depender de core.DATE_FROM_ISO (esa es la ventana fija de 60d que usa el
    fetch en vivo; acá la ventana es dinámica según el cursor de sync)."""
    base = f"https://api.tiendanube.com/v1/{cfg['store_id']}"
    orders, page = [], 1
    while True:
        r = requests.get(f"{base}/orders", headers=core.tn_headers(cfg), params={
            "per_page": 200, "page": page, "updated_at_min": since_iso,
            "fields": "id,created_at,status,payment_status,products",
        }, timeout=60)
        if r.status_code != 200:
            core.tnlog(f"  ⚠ TN orders (sync) error {r.status_code} [{cfg['label']}]: {r.text[:200]}")
            break
        batch = r.json()
        if not batch:
            break
        for o in batch:
            if o.get("payment_status") in ("paid", "authorized") and o.get("status") != "cancelled":
                orders.append(o)
        if len(batch) < 200:
            break
        page += 1
        time.sleep(0.5)
    return orders


def _ml_orders_updated_since(key, cfg, since_iso, until_iso):
    """Órdenes de ML actualizadas en [since_iso, until_iso] — para sync
    incremental el volumen diario es chico, así que no hace falta la lógica
    de subdivisión por volumen que sí necesita el fetch en vivo de 60d
    (core.ml_get_orders). Corta con un aviso si el offset se va de rango
    (indicaría un volumen inusual para una corrida diaria)."""
    token = core.ml_ensure_token(key, cfg)
    user_id = cfg["user_id"]
    offset, limit = 0, 50
    orders = []
    while True:
        data = core.ml_get("https://api.mercadolibre.com/orders/search", token, params={
            "seller": user_id, "order.status": "paid",
            "order.date_last_updated.from": since_iso,
            "order.date_last_updated.to": until_iso,
            "sort": "date_asc", "offset": offset, "limit": limit,
        })
        if not data:
            break
        batch = data.get("results", [])
        if not batch:
            break
        orders.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.35)
        if offset > 5000:
            core.tnlog(f"  ⚠ ML orders (sync) offset > 5000 para {cfg['label']}, "
                       f"cortando — volumen inusual para una corrida incremental")
            break
    return orders


def _dedupe_lineas(lines):
    """Una orden puede traer el mismo (item_id, sku) en más de una línea (ej.
    TN a veces separa la misma unidad en dos entradas de 'products') — el
    unique constraint de repo_ventas_items es por (order_id, item_id, sku), y
    Postgres rechaza un upsert que toque la misma fila dos veces en un solo
    comando. Se suma la cantidad en vez de descartar una de las dos."""
    por_clave = {}
    for l in lines:
        clave = (l["order_id"], l["item_id"], l["sku"])
        if clave in por_clave:
            por_clave[clave]["cantidad"] += l["cantidad"]
        else:
            por_clave[clave] = dict(l)
    return list(por_clave.values())


def _guardar_lineas(config, canal_key, lines, orders_count, notas=None):
    lines = _dedupe_lineas(lines)
    for l in lines:
        l["canal"] = canal_key
    if lines:
        db.upsert(config, "repo_ventas_items", lines, on_conflict="canal,order_id,item_id,sku")
    ahora = datetime.now(timezone.utc).isoformat()
    db.upsert(config, "repo_sync_estado", [{
        "canal": canal_key,
        "cursor_desde": ahora,
        "ultima_corrida": ahora,
        "ordenes_sync": orders_count,
        "notas": notas,
    }], on_conflict="canal")


def sync_incremental(config, canal_key, item_details=None, buffer_days=3):
    """Trae solo las órdenes nuevas/actualizadas desde el último cursor (con
    un buffer hacia atrás para cubrir correcciones tardías de estado, ej. una
    orden que pasa a 'paid' unos días después de creada) y las persiste."""
    cfg = config["channels"][canal_key]
    if not cfg.get("enabled", True):
        return []

    estado = db.select(config, "repo_sync_estado", params={"canal": f"eq.{canal_key}"})
    cursor_previo = estado[0]["cursor_desde"] if estado and estado[0].get("cursor_desde") else None
    ahora = datetime.now(timezone.utc)
    base = datetime.fromisoformat(cursor_previo) if cursor_previo else (ahora - timedelta(days=60))
    desde = base - timedelta(days=buffer_days)

    tipo = CANAL_TIPO[canal_key]
    if tipo == "tn":
        orders = _tn_orders_updated_since(cfg, desde.strftime("%Y-%m-%dT%H:%M:%SZ"))
        lines = core.tn_orders_to_lines(orders)
    else:
        since_iso = desde.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
        until_iso = ahora.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
        orders = _ml_orders_updated_since(canal_key, cfg, since_iso, until_iso)
        lines = core.ml_orders_to_lines(orders, item_details)

    _guardar_lineas(config, canal_key, lines, len(orders))
    core.tnlog(f"  ✓ {canal_key}: sync incremental — {len(orders)} órdenes, {len(lines)} líneas "
               f"(desde {desde.date().isoformat()})")
    return lines


def backfill(config, canal_key, days=365, item_details=None):
    """Carga histórica inicial — reusa tn_get_orders/ml_get_orders tal cual
    (mismo código que el fetch en vivo), solo con una ventana grande."""
    cfg = config["channels"][canal_key]
    tipo = CANAL_TIPO[canal_key]
    core.configure(days=days, coverage_days=core.COVERAGE_DAYS)

    if tipo == "tn":
        orders = core.tn_get_orders(cfg)
        lines = core.tn_orders_to_lines(orders)
    else:
        orders, _token = core.ml_get_orders(canal_key, cfg)
        lines = core.ml_orders_to_lines(orders, item_details)

    _guardar_lineas(config, canal_key, lines, len(orders), notas=f"backfill {days}d")
    core.tnlog(f"✓ Backfill {canal_key}: {len(orders)} órdenes, {len(lines)} líneas")
    return lines
