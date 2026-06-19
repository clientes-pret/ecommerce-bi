#!/usr/bin/env python3
"""
Reporte de Inventario — Pret a Home (Tienda Nube)
==================================================
Columnas: SKU | Unidades | Costo | Costo Total | Precio | Precio Promocional
          Potencial Económico | Fecha Últ. Actualiz. Stock | Fecha Publicación

Uso:
    pip install requests
    python3 reporte_inventario_pah.py

Genera: reporte_inventario_pah.csv (listo para importar a Google Sheets)
"""

import requests
import time
import csv
from datetime import datetime

# ── Credenciales ──────────────────────────────────────────────────────────────
STORE_ID     = "2625285"
ACCESS_TOKEN = "7bf4cde46764d96772079d8cb1d10cd644aa35a0"
HEADERS = {
    "Authentication": f"bearer {ACCESS_TOKEN}",
    "User-Agent": "PretAHome Analytics (info@pretahome.com.ar)",
    "Content-Type": "application/json",
}
BASE_URL    = f"https://api.tiendanube.com/v1/{STORE_ID}"
OUTPUT_FILE = "reporte_inventario_pah.csv"

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_all_pages(endpoint):
    results = []
    page = 1
    while True:
        resp = requests.get(
            f"{BASE_URL}/{endpoint}",
            headers=HEADERS,
            params={"per_page": 200, "page": page},
        )
        if resp.status_code == 429:
            print("  Rate limit, esperando 5s...")
            time.sleep(5)
            continue
        if resp.status_code != 200:
            print(f"  ERROR {resp.status_code}: {resp.text[:200]}")
            break
        data = resp.json()
        if not data:
            break
        results.extend(data)
        print(f"  /{endpoint} — página {page}: {len(data)} registros")
        if len(data) < 200:
            break
        page += 1
        time.sleep(0.5)
    return results


def fmt_date(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return iso_str[:10]


def fmt_num(val):
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return ""


# ── 1. Traer productos ────────────────────────────────────────────────────────

print("\n=== Reporte Inventario Pret a Home ===\n")
print("1. Descargando productos...")
products = get_all_pages("products")
print(f"   Total productos: {len(products)}\n")


# ── 2. Construir filas ────────────────────────────────────────────────────────

print("2. Procesando variantes...")
rows = []
skus_seen = set()

for product in products:
    pub_date = fmt_date(product.get("published_at") or product.get("created_at") or "")

    for variant in product.get("variants", []):
        sku = (variant.get("sku") or "").strip()
        if not sku or sku in skus_seen:
            continue
        skus_seen.add(sku)

        stock = 0
        try:
            stock = int(variant.get("stock") or 0)
        except (TypeError, ValueError):
            pass

        cost        = fmt_num(variant.get("cost_price") or variant.get("cost") or 0)
        price       = fmt_num(variant.get("price", 0))
        promo_price = fmt_num(variant.get("promotional_price") or variant.get("compare_at_price") or 0)

        cost_total = round(cost * stock, 2)        if isinstance(cost, float)        else ""
        potencial  = round(promo_price * stock, 2) if (isinstance(promo_price, float) and promo_price > 0) else ""

        # updated_at de la variante — se actualiza con cada cambio de stock (venta TN, ML, ajuste manual)
        ultima_actualizacion = fmt_date(variant.get("updated_at") or "")

        rows.append({
            "SKU":                          sku,
            "Unidades":                     stock,
            "Costo":                        cost,
            "Costo Total":                  cost_total,
            "Precio":                       price,
            "Precio Promocional":           promo_price,
            "Potencial Económico":          potencial,
            "Fecha Últ. Actualiz. Stock":   ultima_actualizacion,
            "Fecha Publicación":            pub_date,
        })

rows.sort(key=lambda r: r["SKU"])
print(f"   SKUs únicos procesados: {len(rows)}\n")


# ── 3. Totales ────────────────────────────────────────────────────────────────

total_units     = sum(r["Unidades"] for r in rows)
total_cost      = round(sum(r["Costo Total"]         for r in rows if isinstance(r["Costo Total"], float)), 2)
total_potencial = round(sum(r["Potencial Económico"] for r in rows if isinstance(r["Potencial Económico"], float)), 2)

fila_total = {
    "SKU":                        "** TOTALES **",
    "Unidades":                   total_units,
    "Costo":                      "",
    "Costo Total":                total_cost,
    "Precio":                     "",
    "Precio Promocional":         "",
    "Potencial Económico":        total_potencial,
    "Fecha Últ. Actualiz. Stock": "",
    "Fecha Publicación":          "",
}


# ── 4. Escribir CSV ───────────────────────────────────────────────────────────

print("3. Generando CSV...")
fieldnames = [
    "SKU", "Unidades", "Costo", "Costo Total",
    "Precio", "Precio Promocional", "Potencial Económico",
    "Fecha Últ. Actualiz. Stock", "Fecha Publicación",
]

with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    writer.writerow({k: "" for k in fieldnames})
    writer.writerow(fila_total)

print(f"\n✅ Archivo generado: {OUTPUT_FILE}")
print(f"   SKUs únicos : {len(rows)}")
print(f"   Unidades    : {total_units:,}")
print(f"   Costo Total : $ {total_cost:,.2f}")
print(f"   Potencial   : $ {total_potencial:,.2f}")
print(f"\n→ Importar en Google Sheets:")
print(f"   Archivo → Importar → Subir → seleccioná '{OUTPUT_FILE}'")
print(f"   Separador: coma")
