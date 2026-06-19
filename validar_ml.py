#!/usr/bin/env python3
"""
validar_ml.py — Diagnóstico de fetch de órdenes ML
Compara lo que traemos vs lo que ML reporta, y prueba el endpoint correcto con filtro de fechas.

Uso: python3 validar_ml.py
"""

import json, requests, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

# ─── CONFIG ──────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
with open(ROOT / "config.json") as f:
    CONFIG = json.load(f)

DAYS = 60
NOW = datetime.now(timezone.utc)
DATE_FROM = NOW - timedelta(days=DAYS)
DATE_FROM_STR = DATE_FROM.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
DATE_TO_STR   = NOW.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

def ml_refresh(cfg):
    r = requests.post("https://api.mercadolibre.com/oauth/token", data={
        "grant_type":    "refresh_token",
        "client_id":     cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "refresh_token": cfg["refresh_token"],
    }, timeout=15)
    if r.status_code == 200:
        return r.json()["access_token"]
    return cfg["access_token"]

def ml_get(url, token, params=None, retries=5):
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500):
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 401:
                return None
        except Exception:
            time.sleep(2)
    return None

def diagnose_channel(key, cfg):
    print(f"\n{'─'*56}")
    print(f"  Canal: {cfg['label']}")
    print(f"{'─'*56}")

    token = ml_refresh(cfg)
    user_id = cfg["user_id"]
    print(f"  Token renovado ✓")

    # ── TEST 1: ¿Cuántas órdenes totales reporta ML? ─────────────────────────
    print(f"\n  [TEST 1] Total órdenes pagadas en los últimos {DAYS} días según ML...")
    data = ml_get("https://api.mercadolibre.com/orders/search", token, params={
        "seller":       user_id,
        "order.status": "paid",
        "order.date_created.from": DATE_FROM_STR,
        "order.date_created.to":   DATE_TO_STR,
        "sort":  "date_desc",
        "offset": 0,
        "limit":  1,
    })
    if data:
        total_ml = data.get("paging", {}).get("total", "?")
        print(f"  → ML reporta: {total_ml} órdenes pagadas en el período")
    else:
        print("  → No se pudo obtener el total")
        total_ml = 0

    # ── TEST 2: Límite del offset (sin filtro de fecha) ───────────────────────
    print(f"\n  [TEST 2] Límite sin filtro de fecha (método anterior)...")
    data2 = ml_get("https://api.mercadolibre.com/orders/search", token, params={
        "seller":       user_id,
        "order.status": "paid",
        "sort":  "date_desc",
        "offset": 0,
        "limit":  1,
    })
    if data2:
        total_sin_filtro = data2.get("paging", {}).get("total", "?")
        max_offset = data2.get("paging", {}).get("limit", "?")
        print(f"  → Total sin filtro: {total_sin_filtro} órdenes")
        print(f"  → Offset máximo permitido por ML: 1000 (documentado)")
        print(f"  → Con paginación de 50: máximo 1000 órdenes recuperables sin filtro de fecha")

    # ── TEST 3: Paginar CON filtro de fecha en chunks ─────────────────────────
    print(f"\n  [TEST 3] Paginando por chunks de 7 días (método correcto para volumen alto)...")

    CHUNK_DAYS = 7
    ML_MAX = 10000
    all_orders = []
    chunk_start = DATE_FROM

    while chunk_start < NOW:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), NOW)
        dfrom = chunk_start.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
        dto   = chunk_end.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

        probe = ml_get("https://api.mercadolibre.com/orders/search", token, params={
            "seller": user_id, "order.status": "paid",
            "order.date_created.from": dfrom,
            "order.date_created.to":   dto,
            "sort": "date_asc", "offset": 0, "limit": 1,
        })
        if not probe:
            chunk_start = chunk_end
            continue

        chunk_total = probe.get("paging", {}).get("total", 0)

        if chunk_total > ML_MAX:
            mid = chunk_start + (chunk_end - chunk_start) / 2
            print(f"  ⚠ Chunk {chunk_start.strftime('%d/%m')}→{chunk_end.strftime('%d/%m')} tiene {chunk_total} órdenes — subdividiendo")
            CHUNK_DAYS = max(1, CHUNK_DAYS // 2)
            continue

        for chunk_attempt in range(3):
            offset, limit = 0, 50
            chunk_orders = []
            chunk_ok = True
            while offset < chunk_total:
                data = ml_get("https://api.mercadolibre.com/orders/search", token, params={
                    "seller": user_id, "order.status": "paid",
                    "order.date_created.from": dfrom,
                    "order.date_created.to":   dto,
                    "sort": "date_asc", "offset": offset, "limit": limit,
                })
                if not data:
                    chunk_ok = False
                    break
                batch = data.get("results", [])
                if not batch:
                    break
                chunk_orders.extend(batch)
                if len(batch) < limit:
                    break
                offset += limit
                time.sleep(0.35)

            if chunk_ok and len(chunk_orders) >= chunk_total * 0.98:
                break
            elif chunk_attempt < 2:
                print(f"  ↩ Chunk {chunk_start.strftime('%d/%m')}→{chunk_end.strftime('%d/%m')} incompleto ({len(chunk_orders)}/{chunk_total}), reintentando...")
                time.sleep(5)
            else:
                print(f"  ⚠ Chunk {chunk_start.strftime('%d/%m')}→{chunk_end.strftime('%d/%m')} incompleto tras 3 intentos ({len(chunk_orders)}/{chunk_total})")

        all_orders.extend(chunk_orders)
        print(f"  ✓ {chunk_start.strftime('%d/%m')}→{chunk_end.strftime('%d/%m')}: {len(chunk_orders)}/{chunk_total} órdenes")
        chunk_start = chunk_end
        time.sleep(0.3)

    orders_con_filtro = all_orders
    print(f"  → TOTAL recuperadas: {len(orders_con_filtro)} órdenes")

    # Contar unidades
    total_units = 0
    items_counter = defaultdict(int)
    for o in orders_con_filtro:
        for item in o.get("order_items", []):
            qty = item.get("quantity", 1)
            total_units += qty
            title = item.get("item", {}).get("title", "?")[:40]
            items_counter[title] += qty

    print(f"  → Unidades totales vendidas: {total_units}")

    print(f"\n  [TOP 10 productos por unidades]")
    for title, qty in sorted(items_counter.items(), key=lambda x: -x[1])[:10]:
        print(f"    {qty:>6}  {title}")

    # ── RESUMEN ───────────────────────────────────────────────────────────────
    print(f"\n  {'━'*50}")
    print(f"  RESUMEN {cfg['label']}:")
    print(f"    ML reporta:              {total_ml} órdenes")
    print(f"    Recuperamos (con filtro): {len(orders_con_filtro)} órdenes")
    if isinstance(total_ml, int) and total_ml > 0:
        cobertura = len(orders_con_filtro) / total_ml * 100
        print(f"    Cobertura:               {cobertura:.1f}%")
        if cobertura < 95:
            print(f"    ⚠ ATENCIÓN: hay {total_ml - len(orders_con_filtro)} órdenes sin recuperar")
        else:
            print(f"    ✓ Cobertura completa")
    print(f"  {'━'*50}")

    return orders_con_filtro

# ─── MAIN ─────────────────────────────────────────────────────────────────────

print(f"\n{'═'*56}")
print(f"  VALIDACIÓN ML — {DAYS} días")
print(f"  {DATE_FROM.strftime('%d/%m/%Y')} → {NOW.strftime('%d/%m/%Y')}")
print(f"{'═'*56}")

for key in ["ml_pret", "ml_lavan"]:
    cfg = CONFIG["channels"].get(key)
    if cfg and cfg.get("enabled", True):
        diagnose_channel(key, cfg)

print(f"\n{'═'*56}")
print(f"  Diagnóstico completo.")
print(f"{'═'*56}\n")
