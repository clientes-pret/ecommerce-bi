#!/usr/bin/env python3
"""Diagnóstico rápido de endpoints ML"""
import json, requests
from pathlib import Path

ROOT = Path(__file__).parent
with open(ROOT / "config.json") as f:
    CONFIG = json.load(f)
cfg = CONFIG["channels"]["ml_pret"]

# Refresh token
r = requests.post("https://api.mercadolibre.com/oauth/token", data={
    "grant_type": "refresh_token",
    "client_id": cfg["client_id"],
    "client_secret": cfg["client_secret"],
    "refresh_token": cfg["refresh_token"],
}, timeout=15)
token = r.json()["access_token"]
print(f"Token: OK")

headers = {"Authorization": f"Bearer {token}"}
user_id = cfg["user_id"]

# Test 1: items con offset 1000
print("\n[TEST 1] items/search offset=1000")
r = requests.get(f"https://api.mercadolibre.com/users/{user_id}/items/search",
    headers=headers, params={"offset": 1000, "limit": 5, "status": "active"})
print(f"  Status: {r.status_code}")
d = r.json()
print(f"  Paging: {d.get('paging')}")
print(f"  Results: {len(d.get('results',[]))} items")

# Test 2: items con offset 2000
print("\n[TEST 2] items/search offset=2000")
r = requests.get(f"https://api.mercadolibre.com/users/{user_id}/items/search",
    headers=headers, params={"offset": 2000, "limit": 5, "status": "active"})
print(f"  Status: {r.status_code}")
d = r.json()
print(f"  Paging: {d.get('paging')}")
print(f"  Results: {len(d.get('results',[]))} items — {d.get('results',[][:3])}")
print(f"  Error: {d.get('error','ninguno')}")

# Test 3: visitas de un item con ventas
print("\n[TEST 3] visitas de un item conocido")
# Tomar un item de las órdenes recientes
r2 = requests.get("https://api.mercadolibre.com/orders/search", headers=headers,
    params={"seller": user_id, "order.status": "paid", "sort": "date_desc",
            "offset": 0, "limit": 1})
item_id = r2.json()["results"][0]["order_items"][0]["item"]["id"]
print(f"  Item ID de prueba: {item_id}")

for endpoint, params in [
    (f"/items/{item_id}/visits", {"date_from": "2026-05-11T00:00:00.000-00:00", "date_to": "2026-05-21T00:00:00.000-00:00"}),
    (f"/visits/items", {"ids": item_id, "date_from": "2026-05-11T00:00:00.000-00:00", "date_to": "2026-05-21T00:00:00.000-00:00"}),
]:
    r3 = requests.get(f"https://api.mercadolibre.com{endpoint}", headers=headers, params=params)
    print(f"  {endpoint}: {r3.status_code} → {str(r3.text[:200])}")

# Test 4: visits con formato de fecha correcto
print("\n[TEST 4] visitas batch con fecha sin timezone")
from datetime import datetime, timedelta, timezone
NOW = datetime.now(timezone.utc)
DATE_FROM = NOW - timedelta(days=10)
dfrom = DATE_FROM.strftime("%Y-%m-%d")
dto   = NOW.strftime("%Y-%m-%d")

r4 = requests.get("https://api.mercadolibre.com/visits/items", headers=headers,
    params={"ids": item_id, "date_from": dfrom, "date_to": dto})
print(f"  /visits/items (fecha simple): {r4.status_code} → {r4.text[:200]}")

# Test 5: items con scroll_id
print("\n[TEST 5] items con search_type=scan")
r5 = requests.get(f"https://api.mercadolibre.com/users/{user_id}/items/search",
    headers=headers, params={"search_type": "scan", "limit": 100, "status": "active"})
print(f"  Status: {r5.status_code}")
d5 = r5.json()
print(f"  scroll_id: {d5.get('scroll_id','no disponible')}")
print(f"  Results: {len(d5.get('results',[]))}")

# Test 6: offset 1500 (entre 1000 y 2000)
print("\n[TEST 6] items/search offset=1500")
r6 = requests.get(f"https://api.mercadolibre.com/users/{user_id}/items/search",
    headers=headers, params={"offset": 1500, "limit": 5, "status": "active"})
print(f"  Status: {r6.status_code} → {r6.text[:100]}")
