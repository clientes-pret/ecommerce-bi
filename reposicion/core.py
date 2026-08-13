#!/usr/bin/env python3
"""
reposicion/core.py — lógica de negocio de reposición.

Extraído tal cual (mismas fórmulas, mismos umbrales) de generar_reporte.py
mediante ast.get_source_segment — no se reescribió ninguna función de
cálculo. generar_reporte.py sigue existiendo como script CLI de respaldo
(genera los 3 Excel como siempre) durante la transición a esta app.

Uso típico desde un job:

    from reposicion import core
    core.configure(days=60, coverage_days=60)
    core.load_supplier_map()
    results = core.fetch_all(CONFIG["channels"])
    rows = core.build_rows(results)

`rows` es una lista de dicts con las mismas claves (en español) que ya
consume write_excel_reposicion()/write_excel_sobrestock() en
generar_reporte.py — los jobs de esta carpeta las mapean a las tablas
repo_* de Supabase.
"""

import csv, io, json, time, threading, traceback
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ─── VENTANA DE TIEMPO / PARÁMETROS ────────────────────────────────────────────
# Estado de módulo, seteado por configure() antes de fetch_all()/build_rows()
# (mismo valor que generar_reporte.py calculaba a partir de argparse al importar).

DAYS          = 60
COVERAGE_DAYS = 60
NOW           = datetime.now(timezone.utc)
DATE_FROM     = NOW - timedelta(days=DAYS)
DATE_FROM_ISO = DATE_FROM.strftime("%Y-%m-%dT%H:%M:%SZ")
DATE_FROM_STR = DATE_FROM.strftime("%Y-%m-%d")
DATE_TO_STR   = NOW.strftime("%Y-%m-%d")


def configure(days=60, coverage_days=60, now=None):
    """Fija la ventana de ventas (`days`) y el target de cobertura
    (`coverage_days`, default de negocio: 60 — ver repo_settings.coverage_days
    en Supabase, editable desde la app). Llamar antes de fetch_all()/build_rows()."""
    global DAYS, COVERAGE_DAYS, NOW, DATE_FROM, DATE_FROM_ISO, DATE_FROM_STR, DATE_TO_STR
    DAYS          = days
    COVERAGE_DAYS = coverage_days
    NOW           = now or datetime.now(timezone.utc)
    DATE_FROM     = NOW - timedelta(days=DAYS)
    DATE_FROM_ISO = DATE_FROM.strftime("%Y-%m-%dT%H:%M:%SZ")
    DATE_FROM_STR = DATE_FROM.strftime("%Y-%m-%d")
    DATE_TO_STR   = NOW.strftime("%Y-%m-%d")


def tnlog(msg):
    print(f"  {msg}", flush=True)


# ─── PROVEEDORES ────────────────────────────────────────────────────────────
# Prioridad: 1) mapeo explícito SKU→Proveedor desde Google Sheets (o desde
#            repo_productos.proveedor_manual, override que aplican los jobs
#            después de llamar detect_supplier())
#            2) detección por keywords en nombre del producto (fallback)

SKU_SUPPLIER_MAP = {}  # poblado por load_supplier_map(), no al importar el módulo

_DEFAULT_SUPPLIERS_SHEET = (
    "https://docs.google.com/spreadsheets/d/"
    "1_BPBfZ-GdoHwL-vS2rdIfFpYGRdbhzsqNlFmJ1VwLMI"
    "/export?format=csv&gid=0"
)

SUPPLIER_KEYWORDS = [
    ("Home Concept",   ["home concept"]),
    ("Jean Cartier",   ["jean cartier"]),
    ("Franco Valente", ["franco valente"]),
    ("City Blanco",    ["city blanco"]),
    ("Alcoyana",       ["alcoyana"]),
    ("Palette",        ["palette"]),
    ("Sakura",         ["sakura"]),
    ("Pret",           ["pret a home", "pret"]),
]

SUPPLIER_OTHER = "Otros"

def _load_sku_supplier_map(url: str) -> dict:
    """Descarga el Google Sheet como CSV y devuelve {sku_upper: proveedor}."""
    mapping = {}
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text))
        for row in reader:
            sku = (row.get("SKU") or "").strip()
            proveedor = (row.get("Proveedor") or "").strip()
            if sku and proveedor:
                mapping[sku.upper()] = proveedor.strip().title()
        print(f"  ✓ Mapeo SKU→Proveedor cargado: {len(mapping)} SKUs desde Google Sheets")
    except Exception as e:
        print(f"  ⚠ No se pudo cargar el sheet de proveedores ({e}). Se usará solo detección por keywords.")
    return mapping

def detect_supplier(product_name: str, sku: str = "") -> str:
    """Primero busca el SKU en el mapeo explícito; si no está, cae a keywords."""
    if sku:
        result = SKU_SUPPLIER_MAP.get(sku.upper())
        if result:
            return result
    name_lc = product_name.lower()
    for supplier, keywords in SUPPLIER_KEYWORDS:
        if any(kw in name_lc for kw in keywords):
            return supplier
    return SUPPLIER_OTHER

_ml_tokens = {}

def ml_ensure_token(key, cfg):
    if key in _ml_tokens:
        return _ml_tokens[key]
    token = cfg.get("access_token", "")
    refresh = cfg.get("refresh_token")
    if refresh:
        r = requests.post("https://api.mercadolibre.com/oauth/token", data={
            "grant_type":    "refresh_token",
            "client_id":     cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": refresh,
        }, timeout=15)
        if r.status_code == 200:
            token = r.json()["access_token"]
            tnlog(f"✓ Token ML renovado para {cfg['label']}")
        else:
            tnlog(f"⚠ No se pudo renovar token ML {cfg['label']}, usando el guardado")
    _ml_tokens[key] = token
    return token

def ml_get(url, token, params=None, retries=5):
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 500:
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 401:
                return None
        except requests.exceptions.Timeout:
            time.sleep(3 * (attempt + 1))
            continue
        except Exception:
            time.sleep(2)
            continue
    return None

def tn_headers(cfg):
    return {
        "Authentication": f"bearer {cfg['access_token']}",
        "User-Agent":     "EcommerceBi (bi@pretahome.com)",
    }

def tn_get_orders(cfg):
    base = f"https://api.tiendanube.com/v1/{cfg['store_id']}"
    orders, page = [], 1
    TN_TIMEOUT = 60
    TN_RETRIES = 4
    while True:
        r = None
        for attempt in range(TN_RETRIES):
            try:
                r = requests.get(f"{base}/orders", headers=tn_headers(cfg), params={
                    "per_page": 200, "page": page,
                    "created_at_min": DATE_FROM_ISO,
                    "fields": "id,created_at,status,payment_status,products",
                }, timeout=TN_TIMEOUT)
                break
            except requests.exceptions.Timeout:
                if attempt < TN_RETRIES - 1:
                    tnlog(f"  ↩ TN orders timeout page {page} [{cfg['label']}], reintentando "
                          f"({attempt + 1}/{TN_RETRIES})...")
                    time.sleep(3 * (attempt + 1))
                    continue
                tnlog(f"⚠ TN orders timeout definitivo page {page} [{cfg['label']}], "
                      f"me quedo con {len(orders)} ordenes ya traidas.")
                return orders
        if r.status_code == 429:
            time.sleep(3)
            continue
        if r.status_code == 422:
            tnlog(f"  ! TN orders 422 en page={page} [{cfg['label']}] (limite de paginacion), "
                  f"cortando con {len(orders)} ordenes acumuladas.")
            break
        if r.status_code != 200:
            tnlog(f"⚠ TN orders error {r.status_code} [{cfg['label']}]: {r.text[:300]}")
            break
        batch = r.json()
        if not batch:
            break
        for o in batch:
            if o.get("payment_status") in ("paid", "authorized") and o.get("status") != "cancelled":
                orders.append(o)
        if len(batch) < 200:
            break
        page += 1
        time.sleep(0.5)
    return orders

def tn_get_products(cfg):
    base = f"https://api.tiendanube.com/v1/{cfg['store_id']}"
    products, page = [], 1
    while True:
        r = requests.get(f"{base}/products", headers=tn_headers(cfg), params={
            "per_page": 200, "page": page,
        }, timeout=30)
        if r.status_code != 200:
            tnlog(f"⚠ TN products error {r.status_code} [{cfg['label']}]: {r.text[:200]}")
            break
        batch = r.json()
        if not batch:
            break
        products.extend(batch)
        tnlog(f"  TN products page {page}: {len(batch)} productos [{cfg['label']}]")
        if len(batch) < 200:
            break
        page += 1
        time.sleep(0.5)
    return products

def parse_tn_sales(orders):
    """Returns (sales_total, sales_first_half, sales_second_half) dicts keyed by sku."""
    mid = DATE_FROM + (NOW - DATE_FROM) / 2
    sales_total  = defaultdict(int)
    sales_first  = defaultdict(int)
    sales_second = defaultdict(int)
    for o in orders:
        try:
            order_date = datetime.fromisoformat(
                o.get("created_at", "").replace("Z", "+00:00")
            )
        except Exception:
            order_date = NOW
        for item in o.get("products", []):
            sku = str(item.get("sku") or item.get("variant_id") or "")
            qty = item.get("quantity", 1)
            if sku:
                sales_total[sku]  += qty
                if order_date < mid:
                    sales_first[sku]  += qty
                else:
                    sales_second[sku] += qty
    return sales_total, sales_first, sales_second

def ml_get_orders(key, cfg):
    token   = ml_ensure_token(key, cfg)
    user_id = cfg["user_id"]
    label   = cfg["label"]

    CHUNK_DAYS = 7
    ML_MAX     = 10000
    all_orders = []
    chunk_start = DATE_FROM

    while chunk_start < NOW:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), NOW)
        date_from_str = chunk_start.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
        date_to_str   = chunk_end.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

        probe = ml_get("https://api.mercadolibre.com/orders/search", token, params={
            "seller": user_id, "order.status": "paid",
            "order.date_created.from": date_from_str,
            "order.date_created.to":   date_to_str,
            "sort": "date_asc", "offset": 0, "limit": 1,
        })
        if not probe:
            chunk_start = chunk_end
            continue

        chunk_total = probe.get("paging", {}).get("total", 0)

        if chunk_total > ML_MAX:
            tnlog(f"  ⚠ {label}: chunk {chunk_start.strftime('%d/%m')}→{chunk_end.strftime('%d/%m')} tiene {chunk_total} órdenes, subdividiendo...")
            CHUNK_DAYS = max(1, CHUNK_DAYS // 2)
            continue

        MAX_CHUNK_RETRIES = 3
        for chunk_attempt in range(MAX_CHUNK_RETRIES):
            offset, limit = 0, 50
            chunk_orders = []
            chunk_ok = True
            while offset < chunk_total:
                data = ml_get("https://api.mercadolibre.com/orders/search", token, params={
                    "seller": user_id, "order.status": "paid",
                    "order.date_created.from": date_from_str,
                    "order.date_created.to":   date_to_str,
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

            recovered = len(chunk_orders)
            if chunk_ok and recovered >= chunk_total * 0.98:
                break
            else:
                if chunk_attempt < MAX_CHUNK_RETRIES - 1:
                    tnlog(f"  ↩ {label}: chunk incompleto ({recovered}/{chunk_total}), reintentando...")
                    time.sleep(5)
                else:
                    tnlog(f"  ⚠ {label}: chunk incompleto tras {MAX_CHUNK_RETRIES} intentos ({recovered}/{chunk_total})")

        all_orders.extend(chunk_orders)
        tnlog(f"  ✓ {label}: {chunk_start.strftime('%d/%m')}→{chunk_end.strftime('%d/%m')} — {len(chunk_orders)}/{chunk_total} órdenes")
        chunk_start = chunk_end
        time.sleep(0.3)

    tnlog(f"  ✓ {label}: TOTAL {len(all_orders)} órdenes recuperadas en {DAYS} días")
    return all_orders, token

def parse_ml_sales(orders):
    """Returns (sales_total, sales_first_half, sales_second_half) dicts.

    Key strategy (fixes double-count bug):
    - Si el item tiene seller_sku → guardar SOLO como ("sku", sku)
    - Si NO tiene seller_sku    → guardar como ("id", item_id) para resolver después
    Nunca guardar ambos para el mismo item.
    """
    mid = DATE_FROM + (NOW - DATE_FROM) / 2
    sales_total  = defaultdict(int)
    sales_first  = defaultdict(int)
    sales_second = defaultdict(int)

    for o in orders:
        try:
            order_date = datetime.fromisoformat(
                o.get("date_created", "").replace("Z", "+00:00")
            )
        except Exception:
            order_date = NOW

        for item in o.get("order_items", []):
            item_obj = item.get("item", {})
            item_id  = str(item_obj.get("id", ""))
            # ML puede devolver el SKU en seller_sku o seller_custom_field
            sku = str(item_obj.get("seller_sku") or item_obj.get("seller_custom_field") or "").strip()
            qty = item.get("quantity", 1)

            # Usar SKU si existe, si no usar id (se resolverá a SKU más tarde via catálogo)
            if sku:
                key = ("sku", sku)
            elif item_id:
                key = ("id", item_id)
            else:
                continue  # sin identificador, ignorar

            sales_total[key] += qty
            if order_date < mid:
                sales_first[key]  += qty
            else:
                sales_second[key] += qty

    return sales_total, sales_first, sales_second

# ─── APLANADO DE ÓRDENES PARA PERSISTIR (dataset de ventas incremental) ────────
# Estas dos funciones son las únicas piezas nuevas para el dataset de ventas
# persistente — convierten órdenes ya bajadas en vivo a líneas planas para
# guardar en repo_ventas_items. NO tocan parse_tn_sales/parse_ml_sales/
# ml_sales_full_split/build_rows: reposicion/jobs/weekly_calc.py reconstruye
# "órdenes falsas" con la misma forma exacta que esas funciones ya esperan
# (ver fake_tn_orders_from_rows/fake_ml_orders_from_rows ahí), así que la
# lógica de negocio de ventas sigue siendo exactamente la misma tanto si el
# dato viene de la API en vivo como de la tabla persistida.

def tn_orders_to_lines(orders):
    """Aplana órdenes de Tiendanube a líneas {order_id, sku, cantidad, fecha,
    creado_at} para persistir en repo_ventas_items."""
    lines = []
    for o in orders:
        order_id = str(o.get("id", ""))
        if not order_id:
            continue
        try:
            creado_at = datetime.fromisoformat(o.get("created_at", "").replace("Z", "+00:00"))
        except Exception:
            continue
        for item in o.get("products", []):
            sku = str(item.get("sku") or item.get("variant_id") or "")
            if not sku:
                continue
            lines.append({
                "order_id": order_id,
                "item_id": "",
                "sku": sku,
                "cantidad": item.get("quantity", 1),
                "fecha": creado_at.date().isoformat(),
                "creado_at": creado_at.isoformat(),
            })
    return lines

def ml_orders_to_lines(orders, item_details=None):
    """Aplana órdenes de ML a líneas {order_id, item_id, sku, cantidad, fecha,
    creado_at}. Si el order_item no trae seller_sku (común en Full), intenta
    resolver contra item_details ya bajado ese mismo día (mismo criterio que
    ml_item_sku) — evita una llamada extra a la API solo para esto."""
    item_details = item_details or {}
    lines = []
    for o in orders:
        order_id = str(o.get("id", ""))
        if not order_id:
            continue
        try:
            creado_at = datetime.fromisoformat(o.get("date_created", "").replace("Z", "+00:00"))
        except Exception:
            continue
        for item in o.get("order_items", []):
            item_obj = item.get("item", {})
            item_id = str(item_obj.get("id", ""))
            sku = str(item_obj.get("seller_sku") or item_obj.get("seller_custom_field") or "").strip()
            if not sku and item_id in item_details:
                sku = ml_item_sku(item_details[item_id])
            lines.append({
                "order_id": order_id,
                "item_id": item_id,
                "sku": sku,
                "cantidad": item.get("quantity", 1),
                "fecha": creado_at.date().isoformat(),
                "creado_at": creado_at.isoformat(),
            })
    return lines

def ml_get_all_items(key, cfg):
    """Catálogo completo de un canal ML. Usa paginación scroll (sin techo,
    ver ml_scroll_item_ids) en vez de offset — la paginación por offset de
    la API de ML se corta alrededor de las 1000 publicaciones, lo que dejaba
    afuera del cálculo de Full a cualquier catálogo más grande que eso."""
    token   = ml_ensure_token(key, cfg)
    user_id = cfg["user_id"]
    item_ids = ml_scroll_item_ids(token, user_id)
    return ml_items_by_ids(token, item_ids)

def ml_item_sku(body):
    sku = str(body.get("seller_custom_field") or body.get("seller_sku") or "").strip()
    if sku:
        return sku
    # Mismo caso que en ml_stock_by_sku: algunas publicaciones (típicamente
    # Full) solo traen el SKU en attributes/SELLER_SKU, no en los campos
    # clásicos — requiere include_attributes=all en la llamada a /items.
    for attr in (body.get("attributes") or []):
        if attr.get("id") == "SELLER_SKU":
            return str(attr.get("value_name") or "").strip()
    return ""

def ml_stock_by_sku(item_details):
    """A partir del mismo /items ya obtenido (sin llamadas nuevas), separa el
    available_quantity de cada publicación en Full (logistic_type=='fulfillment')
    vs no-Full, agregado por SKU. Devuelve (stock_full, stock_nofull).

    Un mismo inventory_id (pool físico real en el depósito Full) puede estar
    vinculado a varias publicaciones/variaciones distintas (ej. el mismo
    producto publicado en más de una categoría) — sumar available_quantity de
    cada una sin deduplicar infla el stock Full varias veces (confirmado con
    datos reales: 31.643 sumadas vs 14.268 reales). Se cuenta cada
    inventory_id una sola vez; publicaciones sin inventory_id no comparten
    pool con nada, así que se suman como antes."""
    stock_full   = defaultdict(int)
    stock_nofull = defaultdict(int)
    seen_inventory_ids = set()
    for body in item_details.values():
        is_full = (body.get("shipping") or {}).get("logistic_type") == "fulfillment"
        variations = body.get("variations") or []
        if variations:
            for v in variations:
                sku = str(v.get("seller_sku") or v.get("seller_custom_field") or "").strip()
                if not sku:
                    for attr in (v.get("attribute_combinations") or []):
                        if attr.get("id") == "SELLER_SKU":
                            sku = str(attr.get("value_name") or "").strip()
                            break
                if not sku:
                    # Publicaciones Full: el SKU del vendedor no está en
                    # attribute_combinations sino en attributes (variation-level),
                    # y solo viene poblado si se pide con include_attributes=all
                    # (ver ml_items_by_ids). Sin este fallback, ml_stock_by_sku()
                    # descartaba TODO el stock Full silenciosamente.
                    for attr in (v.get("attributes") or []):
                        if attr.get("id") == "SELLER_SKU":
                            sku = str(attr.get("value_name") or "").strip()
                            break
                if not sku:
                    continue
                qty = v.get("available_quantity", 0) or 0
                inventory_id = v.get("inventory_id")
                if is_full and inventory_id:
                    if inventory_id in seen_inventory_ids:
                        continue
                    seen_inventory_ids.add(inventory_id)
                (stock_full if is_full else stock_nofull)[sku] += qty
        else:
            sku = ml_item_sku(body)
            if not sku:
                continue
            qty = body.get("available_quantity", 0) or 0
            inventory_id = body.get("inventory_id")
            if is_full and inventory_id:
                if inventory_id in seen_inventory_ids:
                    continue
                seen_inventory_ids.add(inventory_id)
            (stock_full if is_full else stock_nofull)[sku] += qty
    return stock_full, stock_nofull

def ml_sales_full_split(orders, item_details):
    """Separa las ventas de `orders` en Full vs no-Full según el logistic_type
    ACTUAL de la publicación (foto de hoy, no histórico exacto). Usa el mismo
    item_details ya obtenido — sin llamadas nuevas a la API."""
    logistic_by_id = {
        item_id: (body.get("shipping") or {}).get("logistic_type", "")
        for item_id, body in item_details.items()
    }
    id_to_sku = {item_id: ml_item_sku(body) for item_id, body in item_details.items()}

    sales_full   = defaultdict(int)
    sales_nofull = defaultdict(int)
    for o in orders:
        for item in o.get("order_items", []):
            item_obj = item.get("item", {})
            item_id  = str(item_obj.get("id", ""))
            sku = str(item_obj.get("seller_sku") or item_obj.get("seller_custom_field") or "").strip()
            if not sku:
                sku = id_to_sku.get(item_id, "")
            if not sku:
                continue
            qty = item.get("quantity", 1)
            is_full = logistic_by_id.get(item_id) == "fulfillment"
            (sales_full if is_full else sales_nofull)[sku] += qty
    return sales_full, sales_nofull

def worker_tn(key, cfg):
    label = cfg["label"]
    try:
        tnlog(f"→ {label}: obteniendo órdenes...")
        orders = tn_get_orders(cfg)
        tnlog(f"✓ {label}: {len(orders)} órdenes obtenidas")
        tnlog(f"→ {label}: obteniendo productos...")
        products = tn_get_products(cfg)
        tnlog(f"✓ {label}: {len(products)} productos obtenidos")
        sales_total, sales_first, sales_second = parse_tn_sales(orders)
        tnlog(f"✓ {label}: {len(sales_total)} SKUs con ventas")
        results[key] = {
            "orders": orders,
            "products": products,
            "sales": sales_total,
            "sales_first": sales_first,
            "sales_second": sales_second,
        }
        tnlog(f"✓ {label}: COMPLETO — {len(orders)} órdenes | {len(products)} productos | {len(sales_total)} SKUs con ventas")
    except Exception as e:
        import traceback
        fetch_errors[key] = str(e)
        results[key] = {"orders": [], "products": [], "sales": {},
                        "sales_first": {}, "sales_second": {}}
        tnlog(f"✗ {label}: ERROR — {e}")
        tnlog(f"  Traceback: {traceback.format_exc()}")

def worker_ml(key, cfg):
    label = cfg["label"]
    try:
        tnlog(f"→ {label}: obteniendo órdenes...")
        orders, token = ml_get_orders(key, cfg)
        sales_total, sales_first, sales_second = parse_ml_sales(orders)
        tnlog(f"→ {label}: obteniendo catálogo ML...")
        item_details = ml_get_all_items(key, cfg)
        results[key] = {
            "orders": orders,
            "sales": sales_total,
            "sales_first": sales_first,
            "sales_second": sales_second,
            "item_details": item_details,
        }
        tnlog(f"✓ {label}: {len(orders)} órdenes | {len(item_details)} items en catálogo")
    except Exception as e:
        fetch_errors[key] = str(e)
        results[key] = {"orders": [], "sales": defaultdict(int),
                        "sales_first": defaultdict(int), "sales_second": defaultdict(int),
                        "item_details": {}}
        tnlog(f"✗ {label}: {e}")

def get_name(product):
    n = product.get("name", {})
    if isinstance(n, dict):
        return n.get("es") or n.get("en") or next(iter(n.values()), "")
    return str(n)

def days_active(created_at_str):
    try:
        created = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        if created > NOW:
            return 0
        active_from = max(created, DATE_FROM)
        return max(1, (NOW - active_from).days)
    except Exception:
        return DAYS

def calc_trend(sold_first, sold_second, active_days):
    """Returns (trend_label, trend_pct) comparing 2nd half vs 1st half velocity."""
    half = max(active_days / 2, 1)
    vel1 = sold_first  / half
    vel2 = sold_second / half
    if vel1 == 0 and vel2 == 0:
        return "—", None
    if vel1 == 0:
        return "🚀 Nueva", None
    pct = round((vel2 - vel1) / vel1 * 100, 1)
    if pct >= 20:
        label = f"↑ +{pct:.0f}%"
    elif pct <= -20:
        label = f"↓ {pct:.0f}%"
    else:
        label = f"→ {pct:+.0f}%"
    return label, pct

def units_to_order(stock, vel_diaria, coverage=None):
    """Units needed to reach `coverage` days of stock at current velocity.

    `coverage=None` lee COVERAGE_DAYS al momento de la llamada (no al definir
    la función) para que configure(coverage_days=...) surta efecto incluso
    llamado después de importar el módulo — a diferencia de un default de
    parámetro fijo, que en Python se evalúa una sola vez al cargar el módulo."""
    if coverage is None:
        coverage = COVERAGE_DAYS
    target = vel_diaria * coverage
    need   = max(0, round(target - stock))
    return need

def build_rows(results):
    tn_pret_products  = results.get("tn_pret", {}).get("products", [])
    tn_lavan_products = results.get("tn_lavan", {}).get("products", [])

    tn_pret_sales    = results.get("tn_pret", {}).get("sales", {})
    tn_lavan_sales   = results.get("tn_lavan", {}).get("sales", {})
    tn_pret_first    = results.get("tn_pret", {}).get("sales_first", {})
    tn_pret_second   = results.get("tn_pret", {}).get("sales_second", {})
    tn_lavan_first   = results.get("tn_lavan", {}).get("sales_first", {})
    tn_lavan_second  = results.get("tn_lavan", {}).get("sales_second", {})

    ml_pret_sales    = results.get("ml_pret", {}).get("sales", defaultdict(int))
    ml_lavan_sales   = results.get("ml_lavan", {}).get("sales", defaultdict(int))
    ml_pret_first    = results.get("ml_pret", {}).get("sales_first", defaultdict(int))
    ml_pret_second   = results.get("ml_pret", {}).get("sales_second", defaultdict(int))
    ml_lavan_first   = results.get("ml_lavan", {}).get("sales_first", defaultdict(int))
    ml_lavan_second  = results.get("ml_lavan", {}).get("sales_second", defaultdict(int))

    ml_pret_items    = results.get("ml_pret", {}).get("item_details", {})
    ml_lavan_items   = results.get("ml_lavan", {}).get("item_details", {})

    # ── Stock y ventas ML Full vs no-Full (mismo /items ya obtenido, sin llamadas nuevas)
    # Nota: el stock Full de cada cuenta ML es un pool separado (no se comparte
    # entre marcas), así que a propósito NO se combina ml_pret_stock_full con
    # ml_lavan_stock_full — full_metrics() más abajo usa cada uno por separado.
    ml_pret_stock_full,  ml_pret_stock_nofull  = ml_stock_by_sku(ml_pret_items)
    ml_lavan_stock_full, ml_lavan_stock_nofull = ml_stock_by_sku(ml_lavan_items)

    ml_pret_sales_full,  ml_pret_sales_nofull  = ml_sales_full_split(results.get("ml_pret",  {}).get("orders", []), ml_pret_items)
    ml_lavan_sales_full, ml_lavan_sales_nofull = ml_sales_full_split(results.get("ml_lavan", {}).get("orders", []), ml_lavan_items)
    sales_full_by_sku = defaultdict(int)
    for d in (ml_pret_sales_full, ml_lavan_sales_full):
        for sku, qty in d.items():
            sales_full_by_sku[sku] += qty

    # Full pasa a ser un canal más (como ML Pret/ML Lavan/TN Pret/TN Lavan), así que
    # las ventas "ML Pret"/"ML Lavan" de acá en más son SOLO no-Full — sin esto se
    # contarían dos veces (una en el canal ML de origen, otra en "Full").
    ml_pret_by_sku  = ml_pret_sales_nofull
    ml_lavan_by_sku = ml_lavan_sales_nofull

    def full_metrics_marca(sku, stock_full, active_days, prefix, ventas_dict):
        """Devuelve el dict de columnas Full de UNA sola marca (Pret o Lavan).

        El stock Full de cada cuenta ML es un pool separado que Mercado Libre
        NO comparte entre marcas — a diferencia del depósito (que sí es un
        único stock compartido entre canales). Calcular días-para-quiebre o
        "a reponer" con stock combinado de las dos marcas puede esconder que
        una está por quebrar mientras la otra tiene de sobra, o generar una
        sugerencia de reposición que no dice a qué cuenta ML hay que mandar
        la mercadería. Por eso stock y velocidad van siempre de la mano de
        SU PROPIA marca, nunca mezclados."""
        ventas_full = ventas_dict.get(sku, 0)
        vel_full_diaria  = round(ventas_full / active_days, 4) if active_days > 0 else 0
        vel_full_semanal = round(vel_full_diaria * 7, 2)

        if vel_full_diaria > 0:
            dias_q_full = int(stock_full / vel_full_diaria)
            fecha_q_full = (NOW + timedelta(days=dias_q_full)).strftime("%d/%m/%Y")
        else:
            dias_q_full, fecha_q_full = 9999, "—"

        if ventas_full == 0 and stock_full == 0:
            alerta_full = f"⚫ Sin datos Full {prefix}"
        elif ventas_full == 0:
            alerta_full = f"⚫ Sin ventas Full {prefix}"
        elif stock_full == 0 and vel_full_semanal >= 1:
            alerta_full = f"🔴 SIN STOCK FULL {prefix}"
        elif dias_q_full < 14:
            alerta_full = f"🔴 CRÍTICO FULL {prefix}"
        elif dias_q_full < 30:
            alerta_full = f"🟡 BAJO FULL {prefix}"
        else:
            alerta_full = f"🟢 OK FULL {prefix}"

        a_enviar_full = units_to_order(stock_full, vel_full_diaria) if vel_full_diaria > 0 else 0

        return {
            f"Unid. Full {prefix}":          ventas_full,
            f"Stock Full {prefix}":          stock_full,
            f"Vel. Full {prefix} (diaria)":  vel_full_diaria,
            f"Vel. Full {prefix} (semanal)": vel_full_semanal,
            f"Días quiebre Full {prefix}":   dias_q_full if dias_q_full < 9999 else "—",
            f"Fecha quiebre Full {prefix}":  fecha_q_full,
            f"Alerta Full {prefix}":         alerta_full,
            f"A enviar Full {prefix} (uds)": a_enviar_full,
        }

    def full_metrics(sku, active_days):
        """Mergea las columnas Full de las dos marcas (Pret y Lavan) para un SKU."""
        return {
            **full_metrics_marca(sku, ml_pret_stock_full.get(sku, 0), active_days, "Pret", ml_pret_sales_full),
            **full_metrics_marca(sku, ml_lavan_stock_full.get(sku, 0), active_days, "Lavan", ml_lavan_sales_full),
        }

    def ml_sales_by_sku(ml_sales):
        by_sku = defaultdict(int)
        for (kind, key), qty in ml_sales.items():
            if kind == "sku":
                by_sku[key] += qty
        return by_sku

    ml_pret_1h_sku   = ml_sales_by_sku(ml_pret_first)
    ml_pret_2h_sku   = ml_sales_by_sku(ml_pret_second)
    ml_lavan_1h_sku  = ml_sales_by_sku(ml_lavan_first)
    ml_lavan_2h_sku  = ml_sales_by_sku(ml_lavan_second)

    def build_id_to_sku(item_details):
        d = {}
        for item_id, body in item_details.items():
            sku = body.get("seller_custom_field") or body.get("seller_sku") or ""
            if sku:
                d[item_id] = sku
        return d

    def build_sku_to_permalink(item_details):
        d = {}
        for item_id, body in item_details.items():
            sku       = body.get("seller_custom_field") or body.get("seller_sku") or ""
            permalink = body.get("permalink") or body.get("secure_permalink") or ""
            if sku and permalink:
                d[sku] = permalink
            elif permalink:
                d[item_id] = permalink
        return d

    ml_pret_sku_to_url  = build_sku_to_permalink(ml_pret_items)
    ml_lavan_sku_to_url = build_sku_to_permalink(ml_lavan_items)

    def build_tn_url_map(products):
        d = {}
        for prod in products:
            pid = str(prod.get("id", ""))
            url = prod.get("canonical_url") or prod.get("seo_url") or ""
            if not url:
                handle = prod.get("handle", {})
                if isinstance(handle, dict):
                    handle = handle.get("es") or handle.get("en") or next(iter(handle.values()), "")
                if handle:
                    url = f"/{handle}"
            d[pid] = url
        return d

    tn_pret_url_map  = build_tn_url_map(tn_pret_products)
    tn_lavan_url_map = build_tn_url_map(tn_lavan_products)

    ml_pret_id_to_sku  = build_id_to_sku(ml_pret_items)
    ml_lavan_id_to_sku = build_id_to_sku(ml_lavan_items)

    # Resolver ventas guardadas como id → mapear a SKU (también halves)
    def resolve_id_sales(ml_sales, id_to_sku, by_sku, h1_sku, h2_sku, ml_first, ml_second):
        for (kind, k), qty in ml_sales.items():
            if kind == "id" and k in id_to_sku:
                mapped = id_to_sku[k]
                by_sku[mapped]  += qty
                h1_sku[mapped]  += ml_first.get(("id", k), 0)
                h2_sku[mapped]  += ml_second.get(("id", k), 0)

    # Nota: ml_pret_by_sku / ml_lavan_by_sku ya vienen completos y clasificados
    # (Full vs no-Full) desde ml_sales_full_split() más arriba — no hay que
    # volver a resolverlos acá, solo los half-windows (h1/h2) para "Tendencia",
    # que sí quieren el total combinado. Por eso van a un dict descartable.
    resolve_id_sales(ml_pret_sales,  ml_pret_id_to_sku,  defaultdict(int),
                     ml_pret_1h_sku,  ml_pret_2h_sku,  ml_pret_first,  ml_pret_second)
    resolve_id_sales(ml_lavan_sales, ml_lavan_id_to_sku, defaultdict(int),
                     ml_lavan_1h_sku, ml_lavan_2h_sku, ml_lavan_first, ml_lavan_second)

    # ── Construir mapa de SKU → ventas de TN Lavan (para sumar al row de TN Pret)
    # Índice: sku → (sold_tn_lavan, tn_lavan_1h, tn_lavan_2h, url_tn_lavan)
    tn_lavan_sku_index = {}
    for prod in tn_lavan_products:
        lavan_url_map = tn_lavan_url_map
        prod_id = str(prod.get("id", ""))
        lavan_url = lavan_url_map.get(prod_id, "")
        for variant in prod.get("variants", []):
            sku = str(variant.get("sku") or variant.get("id") or "")
            if not sku:
                continue
            sold = tn_lavan_sales.get(sku, 0)
            sold += tn_lavan_sales.get(str(variant.get("id", "")), 0)
            tn_lavan_sku_index[sku] = {
                "sold": sold,
                "1h":   tn_lavan_first.get(sku, 0),
                "2h":   tn_lavan_second.get(sku, 0),
                "url":  lavan_url,
            }

    # ── SKUs ya procesados (para evitar duplicados)
    seen_skus = set()
    rows = []

    # ── PASO 1: procesar todos los productos de TN Pret (fuente de stock)
    for prod in tn_pret_products:
        product_name     = get_name(prod)
        created_at       = prod.get("created_at", "")
        published        = prod.get("published", False)
        product_id       = str(prod.get("id", ""))
        tn_pret_url      = tn_pret_url_map.get(product_id, "")
        prod_days_active = days_active(created_at)
        is_new           = prod_days_active < DAYS

        for variant in prod.get("variants", []):
            sku        = str(variant.get("sku") or variant.get("id") or "")
            supplier   = detect_supplier(product_name, sku)
            stock      = variant.get("stock", 0) or 0
            price      = float(variant.get("price") or 0)
            cost       = float(variant.get("cost") or 0)
            var_name   = variant.get("values", [{}])
            var_label  = " / ".join(
                v.get("es") or v.get("en") or str(v) if isinstance(v, dict) else str(v)
                for v in var_name
            ) if var_name else ""
            stock_mgmt = variant.get("stock_management", True)

            seen_skus.add(sku)

            # Ventas TN Pret
            sold_tn_pret  = tn_pret_sales.get(sku, 0)
            sold_tn_pret += tn_pret_sales.get(str(variant.get("id", "")), 0)
            tn_p_1h = tn_pret_first.get(sku, 0)
            tn_p_2h = tn_pret_second.get(sku, 0)

            # Ventas TN Lavan (mismo SKU, stock compartido)
            lavan_idx     = tn_lavan_sku_index.get(sku, {})
            sold_tn_lavan = lavan_idx.get("sold", 0)
            tn_l_1h       = lavan_idx.get("1h", 0)
            tn_l_2h       = lavan_idx.get("2h", 0)
            url_tn_lavan  = lavan_idx.get("url", "")

            # Ventas ML (ambas cuentas, sin Full — Full es su propio canal más abajo)
            sold_ml_pret  = ml_pret_by_sku.get(sku, 0)
            sold_ml_lavan = ml_lavan_by_sku.get(sku, 0)
            sold_full     = sales_full_by_sku.get(sku, 0)
            ml_1h = ml_pret_1h_sku.get(sku, 0) + ml_lavan_1h_sku.get(sku, 0)
            ml_2h = ml_pret_2h_sku.get(sku, 0) + ml_lavan_2h_sku.get(sku, 0)

            total_sold = sold_tn_pret + sold_tn_lavan + sold_ml_pret + sold_ml_lavan + sold_full
            total_1h   = tn_p_1h + tn_l_1h + ml_1h
            total_2h   = tn_p_2h + tn_l_2h + ml_2h

            active_days = prod_days_active
            vel_diaria  = round(total_sold / active_days, 4) if active_days > 0 else 0
            vel_semanal = round(vel_diaria * 7, 2)

            # Depósito = todo menos Full (Full tiene su propio quiebre/reposición aparte,
            # ver full_metrics). Así "A reponer" no infla la sugerencia de compra con
            # demanda que ni sale del depósito.
            ventas_deposito = total_sold - sold_full
            vel_dep_diaria  = round(ventas_deposito / active_days, 4) if active_days > 0 else 0
            vel_dep_semanal = round(vel_dep_diaria * 7, 2)

            trend_label, trend_pct = calc_trend(total_1h, total_2h, active_days)

            if vel_dep_diaria > 0 and stock_mgmt:
                dias_quiebre  = int(stock / vel_dep_diaria)
                fecha_quiebre = (NOW + timedelta(days=dias_quiebre)).strftime("%d/%m/%Y")
            else:
                dias_quiebre  = 9999
                fecha_quiebre = "—"

            if not stock_mgmt:
                alerta = "⚪ Sin control"
            elif ventas_deposito == 0:
                alerta = "⚫ Sin ventas"
            elif stock == 0 and vel_dep_semanal >= 1:
                alerta = "🔴 SIN STOCK"
            elif dias_quiebre < 14:
                alerta = "🔴 CRÍTICO"
            elif dias_quiebre < 30:
                alerta = "🟡 BAJO"
            else:
                alerta = "🟢 OK"

            if active_days > 45:
                confianza = "🟢 Alta"
            elif active_days > 20:
                confianza = "🟡 Media"
            else:
                confianza = "🔴 Baja"

            margin_pct  = round((price - cost) / price * 100, 1) if price > 0 else 0
            margin_unit = round(price - cost, 2)
            revenue_60  = round(total_sold * price, 2)
            ganancia_60 = round(total_sold * margin_unit, 2)
            a_reponer   = units_to_order(stock, vel_dep_diaria) if vel_dep_diaria > 0 else 0

            canal_vals = {
                "ML Pret": sold_ml_pret, "ML Lavan": sold_ml_lavan,
                "TN Pret": sold_tn_pret, "TN Lavan": sold_tn_lavan,
                "Full":    sold_full,
            }
            canal_dom = max(canal_vals, key=canal_vals.get) if total_sold > 0 else "—"

            rows.append({
                "SKU":                    sku,
                "Producto":               product_name,
                "Variante":               var_label,
                "Marca":                  "Pret a Home / Casa Lavan",
                "Proveedor":              supplier,
                "Precio TN ($)":          price,
                "Costo ($)":              cost,
                "Margen ($)":             margin_unit,
                "Margen (%)":             margin_pct,
                "Publicado":              "Sí" if published else "No",
                "Fecha creación":         created_at[:10] if created_at else "",
                "Días activo (período)":  active_days,
                "¿Producto nuevo?":       "Sí" if is_new else "No",
                "Confianza métrica":      confianza,
                "Unid. ML Pret":          sold_ml_pret,
                "Unid. ML Lavan":         sold_ml_lavan,
                "Unid. TN Pret":          sold_tn_pret,
                "Unid. TN Lavan":         sold_tn_lavan,
                "Unid. Full":             sold_full,
                "Unid. depósito":         ventas_deposito,
                "Total vendido":          total_sold,
                "Vel. diaria":            vel_diaria,
                "Vel. semanal":           vel_semanal,
                "Vel. diaria depósito":   vel_dep_diaria,
                "Vel. semanal depósito":  vel_dep_semanal,
                "Tendencia":              trend_label,
                "Tendencia (%)":          trend_pct,
                "Stock actual":           stock if stock_mgmt else "Sin ctrl",
                "Días para quiebre":      dias_quiebre if dias_quiebre < 9999 else "—",
                "Fecha quiebre est.":     fecha_quiebre,
                "Alerta stock":           alerta,
                "A reponer (uds)":        a_reponer if vel_diaria > 0 else "—",
                "Canal dominante":        canal_dom,
                "Revenue 60d ($)":        revenue_60,
                "Ganancia bruta 60d ($)": ganancia_60,
                "URL TN Pret":            tn_pret_url,
                "URL TN Lavan":           url_tn_lavan,
                "URL ML Pret":            ml_pret_sku_to_url.get(sku, ""),
                "URL ML Lavan":           ml_lavan_sku_to_url.get(sku, ""),
                **full_metrics(sku, active_days),
            })

    # ── PASO 2: SKUs exclusivos de TN Lavan (no están en TN Pret)
    for prod in tn_lavan_products:
        product_name     = get_name(prod)
        created_at       = prod.get("created_at", "")
        published        = prod.get("published", False)
        product_id       = str(prod.get("id", ""))
        url_tn_lavan     = tn_lavan_url_map.get(product_id, "")
        prod_days_active = days_active(created_at)
        is_new           = prod_days_active < DAYS

        for variant in prod.get("variants", []):
            sku = str(variant.get("sku") or variant.get("id") or "")
            if sku in seen_skus:
                continue  # Ya procesado desde TN Pret
            seen_skus.add(sku)
            supplier = detect_supplier(product_name, sku)

            stock      = variant.get("stock", 0) or 0
            price      = float(variant.get("price") or 0)
            cost       = float(variant.get("cost") or 0)
            var_name   = variant.get("values", [{}])
            var_label  = " / ".join(
                v.get("es") or v.get("en") or str(v) if isinstance(v, dict) else str(v)
                for v in var_name
            ) if var_name else ""
            stock_mgmt = variant.get("stock_management", True)

            sold_tn_lavan  = tn_lavan_sales.get(sku, 0)
            sold_tn_lavan += tn_lavan_sales.get(str(variant.get("id", "")), 0)
            tn_l_1h = tn_lavan_first.get(sku, 0)
            tn_l_2h = tn_lavan_second.get(sku, 0)

            sold_ml_pret  = ml_pret_by_sku.get(sku, 0)
            sold_ml_lavan = ml_lavan_by_sku.get(sku, 0)
            sold_full     = sales_full_by_sku.get(sku, 0)
            ml_1h = ml_pret_1h_sku.get(sku, 0) + ml_lavan_1h_sku.get(sku, 0)
            ml_2h = ml_pret_2h_sku.get(sku, 0) + ml_lavan_2h_sku.get(sku, 0)

            total_sold = sold_tn_lavan + sold_ml_pret + sold_ml_lavan + sold_full
            total_1h   = tn_l_1h + ml_1h
            total_2h   = tn_l_2h + ml_2h

            active_days = prod_days_active
            vel_diaria  = round(total_sold / active_days, 4) if active_days > 0 else 0
            vel_semanal = round(vel_diaria * 7, 2)

            # Depósito = todo menos Full (Full tiene su propio quiebre/reposición aparte,
            # ver full_metrics). Así "A reponer" no infla la sugerencia de compra con
            # demanda que ni sale del depósito.
            ventas_deposito = total_sold - sold_full
            vel_dep_diaria  = round(ventas_deposito / active_days, 4) if active_days > 0 else 0
            vel_dep_semanal = round(vel_dep_diaria * 7, 2)

            trend_label, trend_pct = calc_trend(total_1h, total_2h, active_days)

            if vel_dep_diaria > 0 and stock_mgmt:
                dias_quiebre  = int(stock / vel_dep_diaria)
                fecha_quiebre = (NOW + timedelta(days=dias_quiebre)).strftime("%d/%m/%Y")
            else:
                dias_quiebre  = 9999
                fecha_quiebre = "—"

            if not stock_mgmt:
                alerta = "⚪ Sin control"
            elif ventas_deposito == 0:
                alerta = "⚫ Sin ventas"
            elif stock == 0 and vel_dep_semanal >= 1:
                alerta = "🔴 SIN STOCK"
            elif dias_quiebre < 14:
                alerta = "🔴 CRÍTICO"
            elif dias_quiebre < 30:
                alerta = "🟡 BAJO"
            else:
                alerta = "🟢 OK"

            if active_days > 45:
                confianza = "🟢 Alta"
            elif active_days > 20:
                confianza = "🟡 Media"
            else:
                confianza = "🔴 Baja"

            margin_pct  = round((price - cost) / price * 100, 1) if price > 0 else 0
            margin_unit = round(price - cost, 2)
            revenue_60  = round(total_sold * price, 2)
            ganancia_60 = round(total_sold * margin_unit, 2)
            a_reponer   = units_to_order(stock, vel_dep_diaria) if vel_dep_diaria > 0 else 0

            canal_vals = {
                "ML Pret": sold_ml_pret, "ML Lavan": sold_ml_lavan,
                "TN Pret": 0, "TN Lavan": sold_tn_lavan,
                "Full":    sold_full,
            }
            canal_dom = max(canal_vals, key=canal_vals.get) if total_sold > 0 else "—"

            rows.append({
                "SKU":                    sku,
                "Producto":               product_name,
                "Variante":               var_label,
                "Marca":                  "Casa Lavan",
                "Proveedor":              supplier,
                "Precio TN ($)":          price,
                "Costo ($)":              cost,
                "Margen ($)":             margin_unit,
                "Margen (%)":             margin_pct,
                "Publicado":              "Sí" if published else "No",
                "Fecha creación":         created_at[:10] if created_at else "",
                "Días activo (período)":  active_days,
                "¿Producto nuevo?":       "Sí" if is_new else "No",
                "Confianza métrica":      confianza,
                "Unid. ML Pret":          sold_ml_pret,
                "Unid. ML Lavan":         sold_ml_lavan,
                "Unid. TN Pret":          0,
                "Unid. TN Lavan":         sold_tn_lavan,
                "Unid. Full":             sold_full,
                "Unid. depósito":         ventas_deposito,
                "Total vendido":          total_sold,
                "Vel. diaria":            vel_diaria,
                "Vel. semanal":           vel_semanal,
                "Vel. diaria depósito":   vel_dep_diaria,
                "Vel. semanal depósito":  vel_dep_semanal,
                "Tendencia":              trend_label,
                "Tendencia (%)":          trend_pct,
                "Stock actual":           stock if stock_mgmt else "Sin ctrl",
                "Días para quiebre":      dias_quiebre if dias_quiebre < 9999 else "—",
                "Fecha quiebre est.":     fecha_quiebre,
                "Alerta stock":           alerta,
                "A reponer (uds)":        a_reponer if vel_diaria > 0 else "—",
                "Canal dominante":        canal_dom,
                "Revenue 60d ($)":        revenue_60,
                "Ganancia bruta 60d ($)": ganancia_60,
                "URL TN Pret":            "",
                "URL TN Lavan":           url_tn_lavan,
                "URL ML Pret":            ml_pret_sku_to_url.get(sku, ""),
                "URL ML Lavan":           ml_lavan_sku_to_url.get(sku, ""),
                **full_metrics(sku, active_days),
            })

    return rows

DEAD_STOCK_DAYS   = 60

SLOW_VEL_WEEKLY   = 0.5

SLOW_DIAS_QUIEBRE = 90

OVER_DIAS_QUIEBRE = 60

def capital_inmovilizado(row):
    stock = row.get("Stock actual", 0)
    cost  = row.get("Costo ($)", 0)
    if isinstance(stock, int) and isinstance(cost, (int, float)) and cost > 0:
        return round(stock * cost, 2)
    return 0

def sobrestock_category(row):
    """Returns (category_label, accion) or None if not overstock."""
    stock     = row.get("Stock actual", 0)
    vel_sem   = row.get("Vel. semanal", 0)
    total_sol = row.get("Total vendido", 0)
    dias_q    = row.get("Días para quiebre", 9999)
    stock_mgmt = isinstance(stock, int)  # "Sin ctrl" is str

    if not stock_mgmt or stock == 0:
        return None

    no_ventas  = (total_sol == 0)
    dias_q_int = dias_q if isinstance(dias_q, int) else 9999

    if no_ventas:
        return ("🔴 Stock muerto", "Liquidar / Devolver")
    elif vel_sem < SLOW_VEL_WEEKLY and dias_q_int > SLOW_DIAS_QUIEBRE:
        return ("🟠 Stock lento", "Promocionar activamente")
    elif dias_q_int > OVER_DIAS_QUIEBRE:
        return ("🟡 Sobrestock", "Monitorear / Descuento leve")
    return None

CATEGORY_KEYWORDS = [
    ("Cortinas",                ["cortina"]),
    ("Sábanas",                 ["sábana", "sabana", "sabanas", "sábanas"]),
    ("Acolchados y Edredones",  ["acolchado", "edredón", "edredon", "quilt", "cubrecama"]),
    ("Toallas y Toallones",     ["toallón", "toallon", "toalla"]),
    ("Almohadas",                ["almohada"]),
]
CATEGORY_OTHER = "Bazar"

def classify_product(nombre):
    texto = (nombre or "").lower()
    for cat, keywords in CATEGORY_KEYWORDS:
        if any(kw in texto for kw in keywords):
            return cat
    return CATEGORY_OTHER

def ml_scroll_item_ids(token, user_id):
    """Igual que ml_get_all_items() pero con paginación scroll (search_type=scan)
    en vez de offset — sin techo de resultados. Extraído tal cual de
    stock_full_diario.py:get_all_item_ids(). Usar para el snapshot diario de
    stock, donde el catálogo puede crecer más allá del límite de offset."""
    item_ids  = []
    scroll_id = None
    total     = None

    while True:
        params = {"limit": 100, "status": "active", "search_type": "scan"}
        if scroll_id:
            params["scroll_id"] = scroll_id

        data = ml_get(
            f"https://api.mercadolibre.com/users/{user_id}/items/search",
            token, params=params
        )
        if not data:
            break

        if total is None:
            total = data.get("paging", {}).get("total", 0)
            tnlog(f"  Total publicaciones activas: {total}")

        batch = data.get("results", [])
        if not batch:
            break

        item_ids.extend(batch)
        scroll_id = data.get("scroll_id")

        if len(item_ids) >= total or not scroll_id:
            break
        time.sleep(0.3)

    tnlog(f"  ✓ {len(item_ids)} IDs recuperados (scroll)")
    return list(dict.fromkeys(item_ids))


def ml_items_by_ids(token, item_ids):
    """Detalle completo (/items, batches de 20) para una lista de item_ids ya
    conocida — misma lógica de batching que la segunda mitad de
    ml_get_all_items(), separada para poder combinarla con ml_scroll_item_ids().

    include_attributes=all es necesario para que las variaciones Full traigan
    su SELLER_SKU en variation.attributes — sin este parámetro la API omite
    ese campo y ml_stock_by_sku() no puede resolver el SKU de esas unidades."""
    details = {}
    for i in range(0, len(item_ids), 20):
        batch_ids = item_ids[i:i + 20]
        data = ml_get("https://api.mercadolibre.com/items", token,
                      params={"ids": ",".join(batch_ids), "include_attributes": "all"})
        if data:
            for entry in data:
                if entry.get("code") == 200:
                    body = entry["body"]
                    details[body["id"]] = body
        time.sleep(0.3)
    return details


def load_supplier_map(url=None):
    """Descarga (o recarga) el mapeo SKU→Proveedor. Llamar antes de build_rows()
    si se quiere que detect_supplier() use el Sheet en vez de solo keywords."""
    global SKU_SUPPLIER_MAP
    SKU_SUPPLIER_MAP = _load_sku_supplier_map(url or _DEFAULT_SUPPLIERS_SHEET)
    return SKU_SUPPLIER_MAP


# ─── FETCH MULTI-CANAL (threads) ───────────────────────────────────────────────

results = {}
fetch_errors = {}


def fetch_all(channels):
    """Descarga ventas+stock de todos los canales `enabled` en paralelo
    (mismo patrón de threading que generar_reporte.py main()). Devuelve
    `results` (dict por canal) y `fetch_errors` (dict canal->error).
    Requiere haber llamado configure() antes."""
    global results, fetch_errors
    results = {}
    fetch_errors = {}
    threads = []
    for key, cfg in channels.items():
        if not cfg.get("enabled", True):
            continue
        fn = worker_tn if cfg["type"] == "tiendanube" else worker_ml
        t = threading.Thread(target=fn, args=(key, cfg), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    if fetch_errors:
        tnlog(f"⚠ Errores en canales: {fetch_errors}")
    return results, fetch_errors
