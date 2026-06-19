#!/usr/bin/env python3
"""
Ventas "Edredon Nara" — últimos 90 días — 4 canales
Pret a Home (TN) | Casa Lavan (TN) | Pret a Home (ML) | Casa Lavan (ML)

Uso:
    pip3 install requests
    python3 ventas_edredon_nara.py
"""

import requests, time, sys
from datetime import datetime, timedelta
from collections import defaultdict

# ─── CREDENCIALES ────────────────────────────────────────────────────────────
STORES_TN = {
    "Pret a Home (TN)": {"store_id": "2625285",  "token": "7bf4cde46764d96772079d8cb1d10cd644aa35a0"},
    "Casa Lavan (TN)":  {"store_id": "6709240",   "token": "e01b9ebab49d1e00973700eb3e6a1985b016e4e7"},
}

STORES_ML = {
    "Pret a Home (ML)": {
        "user_id":      "1255615205",
        "client_id":    "1637574709714032",
        "client_secret":"7YEH6ppegi1GbKGu6DhYJptNDeWBjq1s",
        "refresh_token":"TG-69fb975967a2e700015b5b7e-1255615205",
        # Si el access_token está vigente podés pegarlo acá para evitar el refresh:
        "access_token": "",
    },
    "Casa Lavan (ML)": {
        "user_id":      "189036603",
        "client_id":    "1637574709714032",
        "client_secret":"7YEH6ppegi1GbKGu6DhYJptNDeWBjq1s",
        "refresh_token":"TG-69fb97a421cc7f0001145244-189036603",
        "access_token": "",
    },
}

DAYS_BACK = 90
# ─────────────────────────────────────────────────────────────────────────────

def match_nara(title: str) -> bool:
    t = title.lower()
    return "edredon" in t and "nara" in t

def tn_headers(token):
    return {
        "Authentication": f"bearer {token}",
        "User-Agent": "ReporteVentasPola (soporte@pretahome.com)"
    }

def ml_refresh_token(creds: dict) -> str:
    """Renueva el access token de ML usando el refresh token."""
    if creds.get("access_token"):
        # verificar que sigue vigente
        r = requests.get(
            f"https://api.mercadolibre.com/users/{creds['user_id']}",
            headers={"Authorization": f"Bearer {creds['access_token']}"}
        )
        if r.status_code == 200:
            return creds["access_token"]

    print(f"  → Renovando token ML...")
    r = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "refresh_token",
            "client_id":     creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
        }
    )
    if r.status_code != 200:
        print(f"  ✗ Error renovando token: {r.text}")
        return ""
    token = r.json().get("access_token", "")
    print(f"  ✓ Token renovado")
    return token

def fetch_tn_ventas(store_name, creds, date_from):
    sid, token = creds["store_id"], creds["token"]
    base = f"https://api.tiendanube.com/v1/{sid}"
    results = []

    page = 1
    total_orders = 0
    while True:
        r = requests.get(
            f"{base}/orders",
            headers=tn_headers(token),
            params={
                "per_page": 200,
                "page": page,
                "created_at_min": date_from,
                "payment_status": "paid",
                "fields": "id,number,created_at,products,total,status"
            }
        )
        if r.status_code == 401:
            print(f"  ✗ Token expirado — renovar en partners.tiendanube.com")
            break
        if r.status_code != 200:
            print(f"  ✗ Error {r.status_code}: {r.text[:150]}")
            break

        batch = r.json()
        if not batch:
            break

        total_orders += len(batch)
        print(f"  Escaneando... {total_orders} órdenes", end="\r")

        for order in batch:
            if order.get("status") == "cancelled":
                continue
            for item in order.get("products", []):
                if match_nara(item.get("name", "")):
                    variante = " / ".join(item.get("variant_values", [])) or "-"
                    results.append({
                        "canal":       store_name,
                        "orden":       f"#{order.get('number')}",
                        "fecha":       order["created_at"][:10],
                        "producto":    item.get("name"),
                        "variante":    variante,
                        "sku":         item.get("sku") or "-",
                        "cantidad":    int(item.get("quantity", 1)),
                        "precio_unit": float(item.get("price", 0)),
                        "subtotal":    float(item.get("price", 0)) * int(item.get("quantity", 1)),
                    })

        if len(batch) < 200:
            break
        page += 1
        time.sleep(0.5)

    print(f"  ✓ {total_orders} órdenes escaneadas — {len(results)} líneas Edredon Nara")
    return results

def fetch_ml_ventas(store_name, creds, date_from_iso):
    access_token = ml_refresh_token(creds)
    if not access_token:
        return []

    user_id = creds["user_id"]
    hdrs = {"Authorization": f"Bearer {access_token}"}
    results = []
    offset = 0
    total_orders = 0

    while True:
        r = requests.get(
            "https://api.mercadolibre.com/orders/search",
            headers=hdrs,
            params={
                "seller":       user_id,
                "order.status": "paid",
                "sort":         "date_desc",
                "offset":       offset,
                "limit":        50,
            }
        )
        if r.status_code == 401:
            print(f"  ✗ Token ML inválido")
            break
        if r.status_code != 200:
            print(f"  ✗ Error {r.status_code}: {r.text[:150]}")
            break

        data   = r.json()
        orders = data.get("results", [])
        if not orders:
            break

        # stop si el más antiguo ya salió del período
        oldest = orders[-1].get("date_created", "")[:10]
        if oldest < date_from_iso[:10]:
            # procesar los que sí caen en el período
            orders = [o for o in orders if o.get("date_created","")[:10] >= date_from_iso[:10]]
            for order in orders:
                total_orders += 1
                for item in order.get("order_items", []):
                    title = item.get("item", {}).get("title", "")
                    if match_nara(title):
                        results.append({
                            "canal":       store_name,
                            "orden":       str(order.get("id")),
                            "fecha":       order["date_created"][:10],
                            "producto":    title,
                            "variante":    item.get("item", {}).get("variation_attributes", [{}])[0].get("value_name", "-") if item.get("item", {}).get("variation_attributes") else "-",
                            "sku":         item.get("item", {}).get("seller_sku") or "-",
                            "cantidad":    int(item.get("quantity", 1)),
                            "precio_unit": float(item.get("unit_price", 0)),
                            "subtotal":    float(item.get("unit_price", 0)) * int(item.get("quantity", 1)),
                        })
            break

        for order in orders:
            total_orders += 1
            for item in order.get("order_items", []):
                title = item.get("item", {}).get("title", "")
                if match_nara(title):
                    results.append({
                        "canal":       store_name,
                        "orden":       str(order.get("id")),
                        "fecha":       order["date_created"][:10],
                        "producto":    title,
                        "variante":    item.get("item", {}).get("variation_attributes", [{}])[0].get("value_name", "-") if item.get("item", {}).get("variation_attributes") else "-",
                        "sku":         item.get("item", {}).get("seller_sku") or "-",
                        "cantidad":    int(item.get("quantity", 1)),
                        "precio_unit": float(item.get("unit_price", 0)),
                        "subtotal":    float(item.get("unit_price", 0)) * int(item.get("quantity", 1)),
                    })

        print(f"  Escaneando... {total_orders} órdenes", end="\r")
        paging = data.get("paging", {})
        if offset + 50 >= paging.get("total", 0):
            break
        offset += 50
        time.sleep(0.4)

    print(f"  ✓ {total_orders} órdenes escaneadas — {len(results)} líneas Edredon Nara")
    return results

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    date_from = (datetime.now() - timedelta(days=DAYS_BACK))
    date_from_str = date_from.strftime("%Y-%m-%dT00:00:00-03:00")
    print(f"\n{'='*65}")
    print(f"  VENTAS EDREDON POLA — últimos {DAYS_BACK} días")
    print(f"  Desde: {date_from.strftime('%d/%m/%Y')}  |  Hasta: {datetime.now().strftime('%d/%m/%Y')}")
    print(f"{'='*65}\n")

    all_results = []

    # --- Tienda Nube ---
    for name, creds in STORES_TN.items():
        print(f"📦 {name}")
        rows = fetch_tn_ventas(name, creds, date_from_str)
        all_results.extend(rows)
        print()

    # --- Mercado Libre ---
    for name, creds in STORES_ML.items():
        print(f"🛒 {name}")
        rows = fetch_ml_ventas(name, creds, date_from_str)
        all_results.extend(rows)
        print()

    # ─── RESUMEN ─────────────────────────────────────────────────────────────
    if not all_results:
        print("⚠️  No se encontraron ventas de 'Edredon Nara' en el período.")
        return

    # ordenar por fecha desc
    all_results.sort(key=lambda x: x["fecha"], reverse=True)

    print(f"\n{'='*65}")
    print(f"  DETALLE DE VENTAS")
    print(f"{'='*65}")
    print(f"{'CANAL':<22} {'ORDEN':<14} {'FECHA':<12} {'VARIANTE':<18} {'SKU':<14} {'CANT':>5} {'P.UNIT':>10} {'SUBTOTAL':>12}")
    print(f"{'-'*65}{'-'*42}")

    total_unidades = 0
    total_revenue  = 0.0
    por_canal      = defaultdict(lambda: {"unidades": 0, "revenue": 0.0})

    for r in all_results:
        canal    = r["canal"][:21]
        orden    = r["orden"][:13]
        fecha    = r["fecha"]
        variante = r["variante"][:17]
        sku      = r["sku"][:13]
        cant     = r["cantidad"]
        p_unit   = r["precio_unit"]
        subtotal = r["subtotal"]

        print(f"{canal:<22} {orden:<14} {fecha:<12} {variante:<18} {sku:<14} {cant:>5} {p_unit:>10,.0f} {subtotal:>12,.0f}")

        total_unidades += cant
        total_revenue  += subtotal
        por_canal[r["canal"]]["unidades"] += cant
        por_canal[r["canal"]]["revenue"]  += subtotal

    print(f"\n{'='*65}")
    print(f"  RESUMEN POR CANAL")
    print(f"{'='*65}")
    print(f"{'CANAL':<28} {'UNIDADES':>10} {'REVENUE (ARS)':>15}")
    print(f"{'-'*55}")
    for canal, data in sorted(por_canal.items()):
        print(f"{canal:<28} {data['unidades']:>10} {data['revenue']:>15,.0f}")
    print(f"{'-'*55}")
    print(f"{'TOTAL':<28} {total_unidades:>10} {total_revenue:>15,.0f}")
    print(f"{'='*65}\n")

if __name__ == "__main__":
    main()
