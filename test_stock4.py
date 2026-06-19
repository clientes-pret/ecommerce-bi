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

item_id = "MLA2863352664"  # Servilleta con stock Full

# Ver detalle completo
r2 = requests.get(f"https://api.mercadolibre.com/items/{item_id}", headers=headers)
body = r2.json()
print(f"Title: {body.get('title')}")
print(f"available_quantity: {body.get('available_quantity')}")
print(f"inventory_id: {body.get('inventory_id')}")
print(f"logistic_type: {body.get('shipping',{}).get('logistic_type')}")

inv_id = body.get("inventory_id")
print(f"\ninventory_id: {inv_id}")

# Si tiene inventory_id, usarlo
if inv_id:
    for ep in [
        f"/inventories/{inv_id}",
        f"/inventories/{inv_id}/stock",
        f"/inventories/{inv_id}/stock/fulfillment",
    ]:
        r3 = requests.get(f"https://api.mercadolibre.com{ep}", headers=headers)
        print(f"\n  {ep}: {r3.status_code}")
        print(f"  → {r3.text[:300]}")

# También probar con variations
print(f"\nVariations:")
r4 = requests.get(f"https://api.mercadolibre.com/items/{item_id}/variations", headers=headers)
variations = r4.json()
if isinstance(variations, list):
    for v in variations[:2]:
        print(f"  variation_id: {v.get('id')} | inventory_id: {v.get('inventory_id')} | available_quantity: {v.get('available_quantity')}")
        vid = v.get("inventory_id")
        if vid:
            r5 = requests.get(f"https://api.mercadolibre.com/inventories/{vid}/stock/fulfillment", headers=headers)
            print(f"    → fulfillment stock: {r5.status_code}: {r5.text[:200]}")
