#!/usr/bin/env python3
"""
Top 20 Productos Más Vendidos — Tienda Nube Pret a Home
Período: últimos 30 días

Uso:
    python top20_pret_a_home.py --store-id TU_STORE_ID --token TU_ACCESS_TOKEN
    python top20_pret_a_home.py --store-id TU_STORE_ID --token TU_ACCESS_TOKEN --days 60
    python top20_pret_a_home.py --store-id TU_STORE_ID --token TU_ACCESS_TOKEN --output reporte.csv
"""

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    print("❌ Falta el módulo 'requests'. Instalalo con:")
    print("   pip install requests --break-system-packages")
    sys.exit(1)


# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

APP_NAME    = "PretAHome Analytics (admin@pretahome.com.ar)"
BASE_URL    = "https://api.tiendanube.com/v1/{store_id}"
PAGE_SIZE   = 200          # máximo permitido por TN
SLEEP_SEC   = 0.55         # respetar rate limit ~2 req/seg


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def make_headers(token: str) -> dict:
    return {
        "Authentication": f"bearer {token}",
        "User-Agent": APP_NAME,
        "Content-Type": "application/json",
    }


def get_paginated(url: str, headers: dict, params: dict) -> list:
    """Itera sobre todas las páginas de un endpoint TN y devuelve lista completa."""
    results = []
    page = 1
    while True:
        params["page"] = page
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            print("  ⚠️  Rate limit alcanzado, esperando 10s...")
            time.sleep(10)
            continue
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        results.extend(data)
        print(f"  📄 Página {page}: {len(data)} registros (total acumulado: {len(results)})")
        if len(data) < PAGE_SIZE:
            break
        page += 1
        time.sleep(SLEEP_SEC)
    return results


def get_product_name(product_id: int, variant_id: int, products_cache: dict) -> tuple[str, str]:
    """Devuelve (nombre_producto, nombre_variante) desde el cache de productos."""
    prod = products_cache.get(product_id)
    if not prod:
        return f"Producto #{product_id}", f"Variante #{variant_id}"
    
    prod_name = prod.get("name", {})
    if isinstance(prod_name, dict):
        prod_name = prod_name.get("es") or next(iter(prod_name.values()), f"Producto #{product_id}")

    variant_name = f"Variante #{variant_id}"
    for v in prod.get("variants", []):
        if v.get("id") == variant_id:
            values = v.get("values", [])
            if values:
                variant_name = " / ".join(
                    (val.get("es") or next(iter(val.values()), "")) if isinstance(val, dict) else str(val)
                    for val in values
                )
            else:
                variant_name = "Única"
            break

    return prod_name, variant_name


# ──────────────────────────────────────────────
# Lógica principal
# ──────────────────────────────────────────────

def fetch_orders(base: str, headers: dict, date_from: str) -> list:
    """Descarga todas las órdenes pagas desde date_from."""
    url = f"{base}/orders"
    params = {
        "per_page":           PAGE_SIZE,
        "created_at_min":     date_from,
        "payment_status":     "paid",
        "fields":             "id,status,payment_status,products",
    }
    print(f"\n📦 Descargando órdenes desde {date_from[:10]}...")
    orders = get_paginated(url, headers, params)

    # Incluir también payment_status=authorized
    params2 = dict(params)
    params2["payment_status"] = "authorized"
    time.sleep(SLEEP_SEC)
    print("📦 Descargando órdenes con estado 'authorized'...")
    orders += get_paginated(url, headers, params2)

    # Filtrar canceladas
    valid = [o for o in orders if o.get("status") != "cancelled"]
    print(f"✅ Órdenes válidas: {len(valid)}")
    return valid


def fetch_products(base: str, headers: dict) -> dict:
    """Descarga el catálogo completo de productos y devuelve dict {product_id: product}."""
    url = f"{base}/products"
    params = {
        "per_page": PAGE_SIZE,
        "fields": "id,name,variants",
    }
    print("\n🛍️  Descargando catálogo de productos...")
    products_list = get_paginated(url, headers, params)
    return {p["id"]: p for p in products_list}


def aggregate_sales(orders: list) -> dict:
    """
    Agrega ventas por (product_id, variant_id).
    Devuelve dict {(product_id, variant_id): {"qty": int, "revenue": float}}.
    """
    sales = defaultdict(lambda: {"qty": 0, "revenue": 0.0})

    for order in orders:
        for item in order.get("products", []):
            pid = item.get("product_id")
            vid = item.get("variant_id")
            qty = item.get("quantity", 0)
            price = float(item.get("price", 0) or 0)
            sales[(pid, vid)]["qty"] += qty
            sales[(pid, vid)]["revenue"] += qty * price

    return sales


def build_top20(sales: dict, products_cache: dict, top_n: int = 20) -> list:
    """Construye la lista ordenada por cantidad vendida."""
    rows = []
    for (pid, vid), data in sales.items():
        prod_name, variant_name = get_product_name(pid, vid, products_cache)
        rows.append({
            "product_id":   pid,
            "variant_id":   vid,
            "product_name": prod_name,
            "variant":      variant_name,
            "qty_sold":     data["qty"],
            "revenue":      round(data["revenue"], 2),
        })

    rows.sort(key=lambda r: r["qty_sold"], reverse=True)
    return rows[:top_n]


def print_table(rows: list, days: int):
    """Imprime el ranking en la consola."""
    print(f"\n{'='*70}")
    print(f"  🏆  TOP {len(rows)} PRODUCTOS MÁS VENDIDOS — PRET A HOME (últimos {days} días)")
    print(f"{'='*70}")
    print(f"{'#':<4} {'Producto':<35} {'Variante':<18} {'Uds':>6} {'Revenue':>12}")
    print(f"{'-'*70}")

    for i, r in enumerate(rows, 1):
        prod  = r["product_name"][:34]
        var   = r["variant"][:17]
        print(f"{i:<4} {prod:<35} {var:<18} {r['qty_sold']:>6} ${r['revenue']:>11,.2f}")

    total_units   = sum(r["qty_sold"] for r in rows)
    total_revenue = sum(r["revenue"]  for r in rows)
    print(f"{'-'*70}")
    print(f"{'TOTAL':<58} {total_units:>6} ${total_revenue:>11,.2f}")
    print(f"{'='*70}\n")


def save_csv(rows: list, path: str, days: int):
    """Guarda el resultado en un CSV."""
    fieldnames = ["rank", "product_id", "variant_id", "product_name", "variant", "qty_sold", "revenue"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, r in enumerate(rows, 1):
            writer.writerow({"rank": i, **r})
    print(f"💾 CSV guardado en: {path}")


def save_json(rows: list, path: str):
    """Guarda el resultado en JSON."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"💾 JSON guardado en: {path}")


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

PRET_STORE_ID = "2625285"
PRET_TOKEN    = "7bf4cde46764d96772079d8cb1d10cd644aa35a0"


def main():
    parser = argparse.ArgumentParser(
        description="Top 20 productos más vendidos en Tienda Nube — Pret a Home"
    )
    parser.add_argument("--store-id", default=PRET_STORE_ID,
                        help="Store ID de Tienda Nube (default: Pret a Home)")
    parser.add_argument("--token",    default=PRET_TOKEN,
                        help="Access Token de Tienda Nube (default: Pret a Home)")
    parser.add_argument("--days",     type=int, default=30,
                        help="Cantidad de días hacia atrás (default: 30)")
    parser.add_argument("--top",      type=int, default=20,
                        help="Cantidad de productos a mostrar (default: 20)")
    parser.add_argument("--output",   default=None,
                        help="Archivo de salida (.csv o .json). Si no se indica, solo imprime en pantalla.")
    args = parser.parse_args()

    # Rango de fechas
    now       = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=args.days)).strftime("%Y-%m-%dT00:00:00-03:00")

    base    = BASE_URL.format(store_id=args.store_id)
    headers = make_headers(args.token)

    # 1. Validar credenciales rápido
    print(f"\n🔑 Validando credenciales para store {args.store_id}...")
    test = requests.get(f"{base}/store", headers=headers, timeout=15)
    if test.status_code == 401:
        print("❌ Token inválido o expirado. Renovalo en partners.tiendanube.com")
        sys.exit(1)
    test.raise_for_status()
    store_name = test.json().get("name", {})
    if isinstance(store_name, dict):
        store_name = store_name.get("es") or next(iter(store_name.values()), "Pret a Home")
    print(f"✅ Tienda: {store_name}")

    # 2. Traer órdenes y productos
    orders   = fetch_orders(base, headers, date_from)
    time.sleep(SLEEP_SEC)
    products = fetch_products(base, headers)

    # 3. Agregar y rankear
    sales = aggregate_sales(orders)
    top   = build_top20(sales, products, top_n=args.top)

    # 4. Mostrar
    print_table(top, args.days)

    # 5. Exportar si se pidió
    if args.output:
        if args.output.endswith(".json"):
            save_json(top, args.output)
        else:
            save_csv(top, args.output, args.days)


if __name__ == "__main__":
    main()
