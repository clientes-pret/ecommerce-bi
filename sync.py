#!/usr/bin/env python3
"""
sync.py — Motor de sincronización incremental multi-canal

Canales soportados:
  - Mercado Libre (ml_pret, ml_lavan cuando esté disponible)
  - Tienda Nube   (tn_pret, tn_lavan)

Comportamiento:
  - Primera vez: baja todo desde settings.start_date
  - Corridas siguientes: solo baja lo nuevo desde el último sync
  - Cada canal tiene su propio cache → agregar ml_lavan no reprocesa nada

Uso:
  python3 sync.py                      # sync incremental todos los canales
  python3 sync.py --canal ml_pret      # sync solo un canal
  python3 sync.py --full               # reprocesar todo desde start_date
  python3 sync.py --desde 2025-06-01   # sync desde fecha específica

Genera:
  data/cache_{canal}.json   por canal
  data/last_sync.json       timestamps por canal
"""

import os, json, time, argparse
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ──────────────────────────────────────────────────────────────────────────────
# PATHS
# ──────────────────────────────────────────────────────────────────────────────

ROOT      = Path(__file__).parent
DATA_DIR  = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
SYNC_FILE = DATA_DIR / "last_sync.json"

def load_config():
    with open(ROOT / "config.json") as f:
        return json.load(f)

def load_sync_state():
    if SYNC_FILE.exists():
        with open(SYNC_FILE) as f:
            return json.load(f)
    return {}

def save_sync_state(state):
    with open(SYNC_FILE, "w") as f:
        json.dump(state, f, indent=2)

def load_cache(canal_key):
    path = DATA_DIR / f"cache_{canal_key}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"orders": [], "items": {}, "meta": {}}

def save_cache(canal_key, data):
    path = DATA_DIR / f"cache_{canal_key}.json"
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"    Guardado: {path.name} ({size_mb:.1f} MB)")

# ──────────────────────────────────────────────────────────────────────────────
# MERCADO LIBRE
# ──────────────────────────────────────────────────────────────────────────────

ML_BASE = "https://api.mercadolibre.com"

def ml_headers(token):
    return {"Authorization": f"Bearer {token}"}

def ml_refresh_access_token(cfg, canal_key):
    """Renueva el access_token usando el refresh_token y actualiza config.json."""
    print(f"  🔄 Renovando token ML para {cfg['label']}...")
    r = requests.post("https://api.mercadolibre.com/oauth/token",
        headers={"Content-Type": "application/json"},
        json={
            "grant_type":    "refresh_token",
            "client_id":     cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": cfg["refresh_token"]
        }
    )
    if r.status_code != 200:
        print(f"  ❌ Error renovando token: {r.text}")
        return False

    data = r.json()
    cfg["access_token"]     = data["access_token"]
    cfg["refresh_token"]    = data["refresh_token"]
    cfg["token_expires_at"] = int(time.time()) + data["expires_in"] - 300

    config_path = ROOT / "config.json"
    with open(config_path) as f:
        full_config = json.load(f)
    full_config["channels"][canal_key]["access_token"]     = cfg["access_token"]
    full_config["channels"][canal_key]["refresh_token"]    = cfg["refresh_token"]
    full_config["channels"][canal_key]["token_expires_at"] = cfg["token_expires_at"]
    with open(config_path, "w") as f:
        json.dump(full_config, f, indent=2, ensure_ascii=False)

    print(f"  ✅ Token renovado y guardado en config.json")
    return True

def ml_check_token(cfg, canal_key=None):
    """Verifica el token y lo renueva automáticamente si está por vencer o expirado."""
    if cfg.get("refresh_token") and canal_key:
        expires_at = cfg.get("token_expires_at", 0)
        if int(time.time()) >= expires_at:
            if not ml_refresh_access_token(cfg, canal_key):
                return False
            r = requests.get(f"{ML_BASE}/users/{cfg['user_id']}", headers=ml_headers(cfg["access_token"]))
            print(f"  ✅ ML token renovado — {r.json().get('nickname', cfg['user_id'])}")
            return True

    r = requests.get(f"{ML_BASE}/users/{cfg['user_id']}", headers=ml_headers(cfg["access_token"]))
    if r.status_code == 401:
        if cfg.get("refresh_token") and canal_key:
            if ml_refresh_access_token(cfg, canal_key):
                r2 = requests.get(f"{ML_BASE}/users/{cfg['user_id']}", headers=ml_headers(cfg["access_token"]))
                if r2.status_code == 200:
                    print(f"  ✅ ML token renovado — {r2.json().get('nickname', cfg['user_id'])}")
                    return True
        print(f"  ❌ Token ML expirado para {cfg['label']} y no se pudo renovar.")
        return False

    print(f"  ✅ ML token válido — {r.json().get('nickname', cfg['user_id'])}")
    return True

def ml_fetch_orders_week(cfg, date_from, date_to):
    """Trae órdenes para una semana. Si hay +10k divide en días."""
    from_str = date_from.strftime("%Y-%m-%dT00:00:00.000-03:00")
    to_str   = date_to.strftime("%Y-%m-%dT23:59:59.000-03:00")
    orders, offset, limit = [], 0, 50

    while True:
        url = (f"{ML_BASE}/orders/search?seller={cfg['user_id']}"
               f"&order.status=paid"
               f"&order.date_created.from={from_str}"
               f"&order.date_created.to={to_str}"
               f"&sort=date_asc&offset={offset}&limit={limit}")
        r    = requests.get(url, headers=ml_headers(cfg["access_token"]))
        if r.status_code == 401:
            print(f"\n  ❌ Token ML expirado durante sync.")
            return orders, -1
        data    = r.json()
        results = data.get("results", [])
        orders.extend(results)
        total = data.get("paging", {}).get("total", 0)
        if offset + limit >= total or not results or offset >= 9950:
            break
        offset += limit
        time.sleep(0.25)

    return orders, total

def ml_fetch_orders_range(cfg, start_date, end_date):
    """Itera semana a semana. Si una semana tiene +9k, divide en días."""
    all_orders = []
    current    = start_date

    while current <= end_date:
        week_end = min(current + timedelta(days=6), end_date)
        orders, total = ml_fetch_orders_week(cfg, current, week_end)

        if total > 9000:
            # Dividir en días
            print(f"    ⚠ {current.strftime('%Y-%m-%d')} semana con {total} órdenes, dividiendo en días...")
            orders = []
            day = current
            while day <= week_end:
                day_orders, day_total = ml_fetch_orders_week(cfg, day, day)
                orders.extend(day_orders)
                print(f"    → {day.strftime('%Y-%m-%d')}: {day_total} órdenes")
                day += timedelta(days=1)
                time.sleep(0.2)
        else:
            print(f"    → {current.strftime('%Y-%m-%d')} / {week_end.strftime('%m-%d')}: {len(orders)}/{total}  (total: {len(all_orders)+len(orders)})")

        all_orders.extend(orders)
        current = week_end + timedelta(days=1)
        time.sleep(0.2)

    return all_orders

def ml_fetch_active_items(cfg):
    """
    Trae todos los IDs de publicaciones activas usando search_type=scan.
    Evita el límite de offset 1000 de ML.
    """
    ids   = []
    limit = 100
    token = cfg["access_token"]

    # Arrancar con scan desde cero
    url  = f"{ML_BASE}/users/{cfg['user_id']}/items/search?status=active&limit={limit}&search_type=scan"
    r    = requests.get(url, headers=ml_headers(token))
    if r.status_code != 200:
        print(f"    Error al traer ítems: {r.status_code} — {r.text[:200]}")
        return ids

    data      = r.json()
    total     = data.get("paging", {}).get("total", 0)
    scroll_id = data.get("scroll_id")
    results   = data.get("results", [])
    ids.extend(results)
    print(f"    → {len(ids)}/{total} publicaciones activas", end="\r")

    # Paginar con scroll_id hasta agotar resultados
    empty_rounds = 0
    while scroll_id and len(ids) < total:
        url = (f"{ML_BASE}/users/{cfg['user_id']}/items/search"
               f"?status=active&limit={limit}&search_type=scan&scroll_id={scroll_id}")
        r   = requests.get(url, headers=ml_headers(token))
        if r.status_code != 200:
            print(f"\n    Error scroll: {r.status_code}")
            break
        data      = r.json()
        results   = data.get("results", [])
        scroll_id = data.get("scroll_id")

        if not results:
            empty_rounds += 1
            if empty_rounds >= 3:
                break
            time.sleep(1)
            continue

        empty_rounds = 0
        for rid in results:
            if rid not in ids:
                ids.append(rid)
        print(f"    → {len(ids)}/{total} publicaciones activas", end="\r")
        time.sleep(0.3)

    print(f"\n    ✅ {len(ids)}/{total} publicaciones activas")
    return ids

def ml_fetch_item_details(item_ids, token, existing=None):
    details = dict(existing or {})
    ids     = [i for i in set(item_ids) if i not in details]
    if not ids:
        return details
    total = len(ids)
    print(f"    Descargando detalles de {total} items nuevos...")
    for i in range(0, total, 20):
        batch = ids[i:i+20]
        r     = requests.get(f"{ML_BASE}/items?ids={','.join(batch)}",
                             headers=ml_headers(token))
        if r.status_code == 200:
            for item in r.json():
                if item.get("code") == 200:
                    b   = item["body"]
                    iid = b.get("id")
                    if not iid:
                        continue

                    # Try to get SKU from seller_custom_field (item level)
                    sku      = b.get("seller_custom_field") or ""
                    var_skus = {}

                    # If no item-level SKU, try variations
                    if not sku:
                        variations = b.get("variations", [])
                        if variations:
                            for v in variations:
                                vsku = v.get("seller_custom_field") or ""
                                if vsku and not sku:
                                    sku = vsku
                                vid = str(v.get("id", ""))
                                if vid and vsku:
                                    var_skus[vid] = vsku

                    details[iid] = {
                        "title":     b.get("title", ""),
                        "thumbnail": b.get("thumbnail", ""),
                        "permalink": b.get("permalink", ""),
                        "price":     b.get("price", 0),
                        "sku":       sku,
                        "var_skus":  var_skus,
                    }
        print(f"    → {min(i+20,total)}/{total}", end="\r")
        time.sleep(0.3)
    print()
    return details


def ml_fetch_visits(item_ids, token):
    """
    Trae visitas de los últimos 30 días para cada item (1 llamada por item).
    Calcula visits_10d sumando los últimos 10 días del detalle diario.
    Retorna dict: { item_id: { "visits_10d": N, "visits_30d": N } }
    """
    from datetime import datetime, timedelta
    ids    = list(set(item_ids))
    total  = len(ids)
    result = {}

    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    cutoff_10 = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

    print(f"  Descargando visitas 30d de {total} publicaciones...")
    for i, iid in enumerate(ids):
        url = f"{ML_BASE}/items/{iid}/visits?date_from={date_from}&date_to={date_to}"
        r   = requests.get(url, headers=ml_headers(token))
        if r.status_code == 200:
            data     = r.json()
            total_v  = data.get("total_visits", 0)
            # Calcular 10d sumando días recientes del detalle
            detail   = data.get("visits_detail", [])
            # visits_detail no trae día a día, calcular proporcionalmente
            # Si no hay detalle por día, aproximar: 10d = total * (10/30)
            visits_10 = round(total_v * 10 / 30)
            result[iid] = {
                "visits_10d": visits_10,
                "visits_30d": total_v
            }
        print(f"    → {i+1}/{total}", end="\r")
        time.sleep(0.25)

    print(f"\n  ✅ Visitas descargadas: {len(result)} items")
    return result

def sync_ml(canal_key, cfg, since, full=False):
    print(f"\n  [{cfg['label']}]")
    if not ml_check_token(cfg, canal_key):
        return

    cache = load_cache(canal_key)

    # Catálogo actualizado siempre
    print(f"  Catálogo activo...")
    active_ids = ml_fetch_active_items(cfg)
    print(f"  ✅ {len(active_ids)} publicaciones activas")

    # Detalles de items (solo los nuevos)
    all_item_ids = list(set(active_ids))
    cache["items"] = ml_fetch_item_details(all_item_ids, cfg["access_token"], cache.get("items"))

    # Visitas de publicaciones activas — solo si tienen más de 23 horas
    last_visits = cache.get("meta", {}).get("visits_updated")
    visits_age_h = 999
    if last_visits:
        try:
            from datetime import timezone
            age = datetime.now() - datetime.fromisoformat(last_visits)
            visits_age_h = age.total_seconds() / 3600
        except:
            pass
    if visits_age_h >= 23:
        print(f"  Descargando visitas (última vez hace {visits_age_h:.0f}h)...")
        visits = ml_fetch_visits(active_ids, cfg["access_token"])
        for iid, v in visits.items():
            if iid in cache["items"]:
                cache["items"][iid]["visits_10d"] = v["visits_10d"]
                cache["items"][iid]["visits_30d"] = v["visits_30d"]
        cache.setdefault("meta", {})["visits_updated"] = datetime.now().isoformat()
        print(f"  ✅ Visitas actualizadas: {len(visits)} items")
    else:
        print(f"  Visitas OK (actualizadas hace {visits_age_h:.0f}h, próxima en {23-visits_age_h:.0f}h)")

    # Órdenes
    end_date = datetime.now()
    print(f"  Órdenes desde {since.strftime('%Y-%m-%d')}...")
    new_orders = ml_fetch_orders_range(cfg, since, end_date)

    # Detalles de items en órdenes no catalogados
    order_ids = list(set(
        line.get("item", {}).get("id")
        for o in new_orders
        for line in o.get("order_items", [])
        if line.get("item", {}).get("id") and line.get("item", {}).get("id") not in cache["items"]
    ))
    if order_ids:
        print(f"  Items históricos ({len(order_ids)})...")
        cache["items"] = ml_fetch_item_details(order_ids, cfg["access_token"], cache["items"])

    # Merge: eliminar órdenes del período y reemplazar (evita duplicados)
    if not full:
        existing = [o for o in cache.get("orders", [])
                    if o.get("date_created", "")[:10] < since.strftime("%Y-%m-%d")]
    else:
        existing = []

    cache["orders"]          = existing + new_orders
    cache["meta"]["active_ids"] = active_ids
    cache["meta"]["updated"]    = datetime.now().isoformat()

    save_cache(canal_key, cache)
    print(f"  ✅ {len(new_orders)} órdenes nuevas | total: {len(cache['orders'])}")

# ──────────────────────────────────────────────────────────────────────────────
# TIENDA NUBE
# ──────────────────────────────────────────────────────────────────────────────

TN_BASE = "https://api.tiendanube.com/v1"

def tn_headers(cfg):
    return {
        "Authentication": f"bearer {cfg['access_token']}",
        "User-Agent": "EcommerceBIDashboard (matias@pretahome.com)"
    }

def tn_fetch_orders(cfg, since, end_date):
    orders, page = [], 1
    since_str = since.strftime("%Y-%m-%dT00:00:00-03:00")
    end_str   = end_date.strftime("%Y-%m-%dT23:59:59-03:00")

    while True:
        url = (f"{TN_BASE}/{cfg['store_id']}/orders"
               f"?per_page=200&page={page}"
               f"&created_at_min={since_str}"
               f"&created_at_max={end_str}"
               f"&payment_status=paid"
               f"&fields=id,number,created_at,total,products,contact_name,payment_status,shipping_status")
        r = requests.get(url, headers=tn_headers(cfg))
        if r.status_code == 401:
            print(f"\n  ❌ Token TN expirado para {cfg['label']}")
            return orders
        if r.status_code == 429:
            print("  Rate limit TN, esperando 2s...")
            time.sleep(2)
            continue
        data = r.json()
        if not data:
            break
        orders.extend(data)
        print(f"    → {len(orders)} órdenes TN...", end="\r")
        if len(data) < 200:
            break
        page += 1
        time.sleep(0.5)

    print()
    return orders

def tn_fetch_products(cfg):
    """Trae todos los productos con variantes, stock y costo."""
    products, page = {}, 1
    while True:
        url = (f"{TN_BASE}/{cfg['store_id']}/products"
               f"?per_page=200&page={page}"
               f"&fields=id,name,variants,published,categories,created_at"
               f"&published=true")
        r = requests.get(url, headers=tn_headers(cfg))
        if r.status_code == 429:
            time.sleep(2)
            continue
        data = r.json()
        if not data:
            break
        for prod in data:
            pid = str(prod.get("id"))
            name = prod.get("name", {})
            name_str = name.get("es", "") if isinstance(name, dict) else str(name)
            for variant in prod.get("variants", []):
                vid = str(variant.get("id"))
                sku = variant.get("sku") or ""
                vname = variant.get("values", [])
                vname_str = " / ".join(v.get("es", "") if isinstance(v, dict) else str(v) for v in vname) if vname else ""
                full_name = f"{name_str} — {vname_str}" if vname_str else name_str
                products[vid] = {
                    "product_id":   pid,
                    "variant_id":   vid,
                    "title":        full_name,
                    "sku":          sku,
                    "stock":        variant.get("stock", 0) or 0,
                    "price":        float(variant.get("price", 0) or 0),
                    "cost":         float(variant.get("cost", 0) or 0),
                    "published":    prod.get("published", True),
                    "created_at":   (prod.get("created_at") or "")[:10],
                }
        print(f"    → {len(products)} variantes TN...", end="\r")
        if len(data) < 200:
            break
        page += 1
        time.sleep(0.5)

    print()
    return products

def sync_tn(canal_key, cfg, since, full=False):
    print(f"\n  [{cfg['label']}]")
    cache    = load_cache(canal_key)
    end_date = datetime.now()

    # Productos/variantes (siempre frescos — incluye stock y costo actuales)
    print(f"  Catálogo + stock + costos...")
    products = tn_fetch_products(cfg)
    print(f"  ✅ {len(products)} variantes")
    cache["items"] = products

    # Órdenes
    print(f"  Órdenes desde {since.strftime('%Y-%m-%d')}...")
    new_orders = tn_fetch_orders(cfg, since, end_date)

    if not full:
        existing = [o for o in cache.get("orders", [])
                    if o.get("created_at", "")[:10] < since.strftime("%Y-%m-%d")]
    else:
        existing = []

    cache["orders"]          = existing + new_orders
    cache["meta"]["updated"] = datetime.now().isoformat()

    save_cache(canal_key, cache)
    print(f"  ✅ {len(new_orders)} órdenes nuevas | total: {len(cache['orders'])}")

# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync incremental multi-canal")
    parser.add_argument("--canal",         help="Sincronizar solo un canal (ej: ml_pret)")
    parser.add_argument("--full",          action="store_true", help="Reprocesar todo desde start_date")
    parser.add_argument("--desde",         help="Fecha inicio manual (YYYY-MM-DD)")
    parser.add_argument("--solo-catalogo", action="store_true", help="Solo actualizar ítems/catálogo, sin tocar órdenes")
    args = parser.parse_args()

    config     = load_config()
    sync_state = load_sync_state()
    start_cfg  = config["settings"]["start_date"]

    channels = config["channels"]
    if args.canal:
        if args.canal not in channels:
            print(f"Canal '{args.canal}' no encontrado. Disponibles: {list(channels.keys())}")
            return
        channels = {args.canal: channels[args.canal]}

    print(f"\n{'─'*52}")
    print(f"  SYNC — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'─'*52}")

    for key, cfg in channels.items():
        if not cfg.get("enabled"):
            print(f"\n  [{cfg['label']}] — deshabilitado, saltando")
            continue

        # Determinar desde cuándo sincronizar
        if args.full or args.desde:
            since_str = args.desde or start_cfg
        else:
            # Sync incremental: desde el último sync o desde start_date
            last = sync_state.get(key, {}).get("last_sync")
            if last:
                # Restar 1 día para no perder órdenes del día de corte
                since_dt  = datetime.fromisoformat(last) - timedelta(days=1)
                since_str = since_dt.strftime("%Y-%m-%d")
                print(f"\n  [{cfg['label']}] — incremental desde {since_str}")
            else:
                since_str = start_cfg
                print(f"\n  [{cfg['label']}] — primera vez, desde {since_str}")

        since = datetime.strptime(since_str, "%Y-%m-%d")

        try:
            if args.solo_catalogo:
                if cfg["type"] == "tiendanube":
                    print(f"\n  [{cfg['label']}] — actualizando solo catálogo...")
                    cache = load_cache(key)
                    if cache is None:
                        cache = {"orders": [], "items": {}, "meta": {}}
                    products = tn_fetch_products(cfg)
                    cache["items"] = products
                    cache["meta"]["updated"] = datetime.now().isoformat()
                    save_cache(key, cache)
                    print(f"  ✅ {len(products)} variantes actualizadas")
                elif cfg["type"] == "mercadolibre":
                    print(f"\n  [{cfg['label']}] — actualizando solo catálogo ML...")
                    ml_check_token(cfg, key)
                    cache = load_cache(key)
                    if cache is None:
                        cache = {"orders": [], "items": {}, "meta": {}}
                    # Traer IDs activos y sus detalles frescos
                    active_ids   = ml_fetch_active_items(cfg)
                    active_items = ml_fetch_item_details(active_ids, cfg["access_token"], {})

                    # Visitas — solo si tienen más de 23 horas
                    last_visits = cache.get("meta", {}).get("visits_updated")
                    visits_age_h = 999
                    if last_visits:
                        try:
                            age = datetime.now() - datetime.fromisoformat(last_visits)
                            visits_age_h = age.total_seconds() / 3600
                        except:
                            pass
                    if visits_age_h >= 23:
                        visits = ml_fetch_visits(active_ids, cfg["access_token"])
                        for iid, v in visits.items():
                            if iid in active_items:
                                active_items[iid]["visits_10d"] = v["visits_10d"]
                                active_items[iid]["visits_30d"] = v["visits_30d"]
                        cache.setdefault("meta", {})["visits_updated"] = datetime.now().isoformat()
                        print(f"  ✅ Visitas actualizadas: {len(visits)} items")
                    else:
                        print(f"  Visitas OK (hace {visits_age_h:.0f}h)")

                    # Traer detalles de items en órdenes que no están en los activos
                    order_ids = list(set(
                        line.get("item", {}).get("id")
                        for o in cache.get("orders", [])
                        for line in o.get("order_items", [])
                        if isinstance(o, dict) and line.get("item", {}).get("id")
                        and line.get("item", {}).get("id") not in active_items
                    ))
                    if order_ids:
                        print(f"  Items históricos: {len(order_ids)}...")
                        hist_items = ml_fetch_item_details(order_ids, cfg["access_token"], {})
                        active_items.update(hist_items)
                    cache["items"] = active_items
                    cache["meta"]["updated"] = datetime.now().isoformat()
                    save_cache(key, cache)
                    con_sku = sum(1 for i in cache["items"].values() if i.get("sku"))
                    print(f"  ✅ {len(cache['items'])} items | Con SKU: {con_sku}")
            elif cfg["type"] == "mercadolibre":
                sync_ml(key, cfg, since, full=args.full)
            elif cfg["type"] == "tiendanube":
                sync_tn(key, cfg, since, full=args.full)

            # Actualizar timestamp de sync exitoso
            sync_state[key] = {"last_sync": datetime.now().isoformat()}
            save_sync_state(sync_state)

        except Exception as e:
            print(f"\n  ❌ Error en {key}: {e}")
            import traceback; traceback.print_exc()

    print(f"\n{'─'*52}")
    print(f"  Sync completo. Corré: python3 report.py")
    print(f"{'─'*52}\n")

if __name__ == "__main__":
    main()
