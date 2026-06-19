#!/usr/bin/env python3
"""
Valida la cantidad de órdenes por día contra lo que muestra ML.
Corré esto y comparalo con el reporte diario de ML.
"""
import json, requests, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent
with open(ROOT / "config.json") as f:
    CONFIG = json.load(f)
cfg = CONFIG["channels"]["ml_pret"]

r = requests.post("https://api.mercadolibre.com/oauth/token", data={
    "grant_type": "refresh_token",
    "client_id": cfg["client_id"],
    "client_secret": cfg["client_secret"],
    "refresh_token": cfg["refresh_token"],
}, timeout=15)
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}
user_id = cfg["user_id"]

DAYS = 10
NOW  = datetime.now(timezone.utc)
DATE_FROM = NOW - timedelta(days=DAYS)

print(f"\n{'═'*56}")
print(f"  VALIDACIÓN DE ÓRDENES — últimos {DAYS} días")
print(f"  {DATE_FROM.strftime('%d/%m/%Y %H:%M')} → {NOW.strftime('%d/%m/%Y %H:%M')} UTC")
print(f"{'═'*56}\n")

# Obtener total real de ML de una sola vez
probe = requests.get("https://api.mercadolibre.com/orders/search", headers=headers,
    params={
        "seller": user_id, "order.status": "paid",
        "order.date_created.from": DATE_FROM.strftime("%Y-%m-%dT%H:%M:%S.000-00:00"),
        "order.date_created.to":   NOW.strftime("%Y-%m-%dT%H:%M:%S.000-00:00"),
        "sort": "date_asc", "offset": 0, "limit": 1,
    })
total_ml = probe.json().get("paging", {}).get("total", 0)
print(f"  Total órdenes según ML (1 query): {total_ml}")

# Día por día para ver dónde se pierden
print(f"\n  Detalle día por día:")
print(f"  {'Día':<15} {'ML reporta':>12} {'Recuperamos':>12} {'Diferencia':>12}")
print(f"  {'─'*55}")

total_recuperado = 0
total_unidades   = 0
total_revenue    = 0

for d in range(DAYS):
    day_start = DATE_FROM + timedelta(days=d)
    day_end   = DATE_FROM + timedelta(days=d+1)
    if day_end > NOW:
        day_end = NOW

    dfrom = day_start.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
    dto   = day_end.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

    # Total del día
    p = requests.get("https://api.mercadolibre.com/orders/search", headers=headers,
        params={"seller": user_id, "order.status": "paid",
                "order.date_created.from": dfrom, "order.date_created.to": dto,
                "sort": "date_asc", "offset": 0, "limit": 1})
    day_total = p.json().get("paging", {}).get("total", 0)

    # Recuperar todas las órdenes del día
    orders = []
    offset = 0
    while offset < day_total:
        r2 = requests.get("https://api.mercadolibre.com/orders/search", headers=headers,
            params={"seller": user_id, "order.status": "paid",
                    "order.date_created.from": dfrom, "order.date_created.to": dto,
                    "sort": "date_asc", "offset": offset, "limit": 50})
        if r2.status_code != 200: break
        batch = r2.json().get("results", [])
        if not batch: break
        orders.extend(batch)
        if len(batch) < 50: break
        offset += 50
        time.sleep(0.3)

    units = sum(i.get("quantity",1) for o in orders for i in o.get("order_items",[]))
    rev   = sum(float(o.get("total_amount",0)) for o in orders)

    total_recuperado += len(orders)
    total_unidades   += units
    total_revenue    += rev

    diff = len(orders) - day_total
    flag = "⚠" if abs(diff) > 2 else "✓"
    print(f"  {day_start.strftime('%d/%m (%a)'):<15} {day_total:>12} {len(orders):>12} {diff:>+12}  {flag}")
    time.sleep(0.3)

print(f"  {'─'*55}")
print(f"  {'TOTAL':<15} {total_ml:>12} {total_recuperado:>12} {total_recuperado-total_ml:>+12}")
print(f"\n  Unidades recuperadas: {total_unidades:,.0f}")
print(f"  Revenue recuperado:   ${total_revenue:,.0f}")
print(f"\n  ({'✓ OK' if abs(total_recuperado-total_ml) < 10 else '⚠ HAY DIFERENCIA'})")
