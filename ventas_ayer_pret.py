#!/usr/bin/env python3
"""
Ventas Ayer — Pret a Home (Tienda Nube)
Genera un CSV listo para importar en Google Sheets con:
  SKU, Nombre, Stock actual, Precio, Precio promo, % descuento,
  Costo, Qty vendida ayer, Última compra anterior a ayer
"""

import requests, json, time, csv, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ── Credenciales ──────────────────────────────────────────────────────────────
STORE_ID    = "2625285"
TOKEN       = "7bf4cde46764d96772079d8cb1d10cd644aa35a0"
HEADERS     = {
    "Authentication": f"bearer {TOKEN}",
    "User-Agent":     "PretAHome Analytics (admin@pretahome.com.ar)",
}
BASE        = f"https://api.tiendanube.com/v1/{STORE_ID}"

# ── Fechas ────────────────────────────────────────────────────────────────────
TZ_AR       = timezone(timedelta(hours=-3))
NOW         = datetime.now(TZ_AR)
YESTERDAY   = NOW - timedelta(days=1)
DAY_START   = YESTERDAY.replace(hour=0,  minute=0,  second=0,  microsecond=0)
DAY_END     = YESTERDAY.replace(hour=23, minute=59, second=59, microsecond=0)


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_all(endpoint, params=None):
    """Pagina automáticamente y devuelve lista completa."""
    params  = params or {}
    results = []
    page    = 1
    while True:
        params["page"]     = page
        params["per_page"] = 200
        r = requests.get(f"{BASE}/{endpoint}", headers=HEADERS, params=params, timeout=30)
        if r.status_code == 429:
            print("  Rate limit, esperando 10s…")
            time.sleep(10)
            continue
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        results.extend(data)
        if len(data) < 200:
            break
        page += 1
        time.sleep(0.5)
    return results

def fmt_price(val):
    """Formatea número como precio con 2 decimales."""
    if val is None:
        return ""
    try:
        return f"${float(val):,.2f}"
    except Exception:
        return str(val)

def fmt_pct(val):
    if val is None:
        return ""
    try:
        return f"{float(val):.1f}%"
    except Exception:
        return str(val)

# ── 1. Órdenes de ayer ────────────────────────────────────────────────────────
print(f"\n📦 Buscando órdenes del {DAY_START.date()} (hora AR)…")
orders_yesterday = get_all("orders", {
    "created_at_min": DAY_START.isoformat(),
    "created_at_max": DAY_END.isoformat(),
    "fields":         "id,number,status,payment_status,products,created_at",
})

valid_yesterday = [
    o for o in orders_yesterday
    if o.get("payment_status") in ("paid", "authorized")
    and o.get("status") != "cancelled"
]
print(f"  ✓ {len(valid_yesterday)} órdenes válidas ayer (de {len(orders_yesterday)} totales)")

# Qty vendida por variante
sold_qty = defaultdict(int)   # variant_id → qty
for order in valid_yesterday:
    for p in order.get("products", []):
        vid = p.get("variant_id")
        if vid:
            sold_qty[vid] += p.get("quantity", 1)

if not sold_qty:
    print("\n⚠️  No se encontraron ventas válidas ayer. Saliendo.")
    sys.exit(0)

print(f"  ✓ {len(sold_qty)} variantes distintas vendidas")


# ── 3. Datos de productos (stock, precio, costo) ──────────────────────────────
print("\n🏷️  Descargando productos…")
products = get_all("products", {
    "fields": "id,name,variants,published",
})
print(f"  ✓ {len(products)} productos cargados")

# Indexar variantes por su ID
variants_by_id = {}
for prod in products:
    prod_name_es = ""
    # name puede ser dict {es: "...", ...} o string
    name_raw = prod.get("name", "")
    if isinstance(name_raw, dict):
        prod_name_es = name_raw.get("es") or list(name_raw.values())[0]
    else:
        prod_name_es = name_raw

    for v in prod.get("variants", []):
        vid = v.get("id")
        if not vid:
            continue

        # Nombre completo = producto + valores de opción si tiene
        values = v.get("values", [])
        if values:
            option_label = []
            for val in values:
                if isinstance(val, dict):
                    option_label.append(val.get("es") or val.get("name") or str(val))
                else:
                    option_label.append(str(val))
            full_name = f"{prod_name_es} — {' / '.join(option_label)}"
        else:
            full_name = prod_name_es

        variants_by_id[vid] = {
            "product_id":   prod.get("id"),
            "name":         full_name,
            "sku":          v.get("sku") or "",
            "stock":        v.get("stock"),
            "price":        v.get("price"),
            "promo_price":  v.get("promotional_price"),
            "cost":         v.get("cost") or v.get("cost_price"),
        }

# ── 4. Construir filas del reporte ────────────────────────────────────────────
rows = []
yesterday_label = YESTERDAY.strftime("%-d de %B").replace(
    "January","enero").replace("February","febrero").replace("March","marzo").replace(
    "April","abril").replace("May","mayo").replace("June","junio").replace(
    "July","julio").replace("August","agosto").replace("September","septiembre").replace(
    "October","octubre").replace("November","noviembre").replace("December","diciembre")

for vid, qty in sorted(sold_qty.items(), key=lambda x: -x[1]):
    v = variants_by_id.get(vid)
    if not v:
        # variante no encontrada (eliminada?), poner info mínima
        v = {"name": f"[Variante {vid}]", "sku": "", "stock": None,
             "price": None, "promo_price": None, "cost": None}

    price      = v.get("price")
    promo      = v.get("promo_price")
    cost       = v.get("cost")
    stock      = v.get("stock")

    # % descuento
    pct_desc = ""
    if price and promo:
        try:
            pct_desc = fmt_pct((float(price) - float(promo)) / float(price) * 100)
        except Exception:
            pass

    rows.append({
        "SKU":              v.get("sku") or "—",
        "Producto":         v.get("name") or "—",
        "Qty vendida ayer": qty,
        "Stock actual":     stock if stock is not None else "—",
        "Precio":           fmt_price(price),
        "Precio promo":     fmt_price(promo) if promo else "—",
        "% Descuento":      pct_desc or "—",
        "Costo":            fmt_price(cost),
    })

# ── 5. Guardar CSV ────────────────────────────────────────────────────────────
OUT = "ventas_ayer_pret.csv"
COLS = ["SKU","Producto","Qty vendida ayer","Stock actual","Precio",
        "Precio promo","% Descuento","Costo"]

with open(OUT, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig para Excel/Sheets
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    w.writerows(rows)

print(f"\n✅ Reporte generado: {OUT}")
print(f"   {len(rows)} productos — Ventas del {yesterday_label}")
print(f"\n📋 Para importar en Google Sheets:")
print(f"   Archivo → Importar → Subir → seleccioná '{OUT}'")
print(f"   Separador: coma | Tipo de datos: detectar automáticamente\n")
