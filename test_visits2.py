#!/usr/bin/env python3
import json, requests
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

# Tomar un item con ventas reales
r2 = requests.get("https://api.mercadolibre.com/orders/search", headers=headers,
    params={"seller": user_id, "order.status": "paid", "sort": "date_desc", "limit": 5})
item_id = r2.json()["results"][0]["order_items"][0]["item"]["id"]
print(f"Item de prueba: {item_id}")

# Test: time_window con last=7
print("\n[time_window last=7 days]")
r3 = requests.get(f"https://api.mercadolibre.com/items/{item_id}/visits/time_window",
    headers=headers, params={"last": 7, "unit": "day"})
print(f"  Status: {r3.status_code}")
print(f"  Response: {r3.text[:400]}")

# Test: totales del usuario por fecha
print("\n[user items_visits con fecha]")
r4 = requests.get(f"https://api.mercadolibre.com/users/{user_id}/items_visits",
    headers=headers, params={"date_from": "2026-05-15", "date_to": "2026-05-22"})
print(f"  Status: {r4.status_code}")
print(f"  Response: {r4.text[:400]}")

# Test: user items_visits time_window
print("\n[user items_visits/time_window]")
r5 = requests.get(f"https://api.mercadolibre.com/users/{user_id}/items_visits/time_window",
    headers=headers, params={"last": 7, "unit": "day"})
print(f"  Status: {r5.status_code}")
print(f"  Response: {r5.text[:400]}")
