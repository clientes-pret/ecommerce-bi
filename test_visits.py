#!/usr/bin/env python3
import json, requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

NOW = datetime.now(timezone.utc)
DATE_FROM = NOW - timedelta(days=10)
dfrom = DATE_FROM.strftime("%Y-%m-%d")
dto   = NOW.strftime("%Y-%m-%d")

# Tomar 5 items con ventas reales de órdenes
r2 = requests.get("https://api.mercadolibre.com/orders/search", headers=headers,
    params={"seller": user_id, "order.status": "paid",
            "sort": "date_desc", "offset": 0, "limit": 10})
orders = r2.json().get("results", [])
item_ids = []
for o in orders:
    for item in o.get("order_items", []):
        iid = item["item"]["id"]
        if iid not in item_ids:
            item_ids.append(iid)
        if len(item_ids) >= 5:
            break
    if len(item_ids) >= 5:
        break

print(f"Items de prueba: {item_ids}")
print(f"Período: {dfrom} → {dto}")

# Test 1: un item
print(f"\n[1 item]")
r3 = requests.get("https://api.mercadolibre.com/visits/items",
    headers=headers, params={"ids": item_ids[0], "date_from": dfrom, "date_to": dto})
print(f"  Status: {r3.status_code}")
print(f"  Response: {r3.text[:300]}")

# Test 2: 5 items separados por coma
print(f"\n[5 items con coma]")
r4 = requests.get("https://api.mercadolibre.com/visits/items",
    headers=headers, params={"ids": ",".join(item_ids), "date_from": dfrom, "date_to": dto})
print(f"  Status: {r4.status_code}")
print(f"  Response: {r4.text[:300]}")

# Test 3: sin date_from/date_to
print(f"\n[Sin fechas]")
r5 = requests.get("https://api.mercadolibre.com/visits/items",
    headers=headers, params={"ids": item_ids[0]})
print(f"  Status: {r5.status_code}")
print(f"  Response: {r5.text[:300]}")

# Test 4: date_from como timestamp
print(f"\n[Con timestamp]")
r6 = requests.get("https://api.mercadolibre.com/visits/items",
    headers=headers, params={
        "ids": item_ids[0],
        "date_from": DATE_FROM.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "date_to":   NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    })
print(f"  Status: {r6.status_code}")
print(f"  Response: {r6.text[:300]}")
