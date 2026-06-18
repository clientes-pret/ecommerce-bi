#!/usr/bin/env python3
"""
logistica/api/sync_pedidos.py
Importa pedidos pendientes de ML y TN a la tabla log_pedidos.
Diseñado para correr cada 5 minutos (cron o manual).
"""

import json
import requests
import psycopg2
import psycopg2.extras
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).parent.parent.parent
with open(ROOT / "config.json") as f:
    cfg = json.load(f)

DB_URL = cfg["database"]["url"]
CHANNELS = cfg["channels"]

# ── Mapeo de tipo de envío → correo_id ───────────────────────────────────────

def mapear_correo_ml(order: dict) -> str:
    """Determina el correo a partir del shipping de ML."""
    shipping = order.get("shipping", {}) or {}
    logistica = order.get("order_items", [{}])[0].get("item", {}).get("listing_type_id", "")
    tags = order.get("tags", []) or []
    modo = shipping.get("shipping_mode", "")
    substatus = shipping.get("substatus", "")

    if "fulfillment" in modo.lower():
        return "flex"  # Full (no debería llegar, pero por si acaso)
    if "flex" in tags or "flex" in str(shipping).lower():
        return "flex"
    if "me2" in modo.lower() or "colecta" in str(shipping).lower():
        return "colecta"
    return "colecta"  # default ML

def mapear_correo_tn(order: dict) -> str:
    """Determina el correo a partir del shipping de TN.
    En TN, 'shipping' es un string ID y 'shipping_option' es el nombre del correo.
    """
    nombre = (order.get("shipping_option") or "").lower()

    if "retira" in nombre or "local" in nombre or "pickup" in nombre:
        return "retira_local"
    if "cabify" in nombre:
        return "cabify"
    if "andreani" in nombre:
        return "andreani"
    if "correo" in nombre or "oca" in nombre:
        return "correo_arg"
    if "mauro" in nombre:
        return "mauro"
    return "correo_arg"  # default TN

# ── Helpers de token ML ───────────────────────────────────────────────────────

def refresh_ml_token(canal_key: str) -> str:
    """Renueva el access_token de ML si es necesario."""
    ch = CHANNELS[canal_key]
    now_ts = datetime.now(timezone.utc).timestamp()
    if ch.get("token_expires_at", 0) > now_ts + 300:
        return ch["access_token"]

    print(f"  Renovando token {canal_key}...")
    r = requests.post("https://api.mercadolibre.com/oauth/token", data={
        "grant_type": "refresh_token",
        "client_id": ch["client_id"],
        "client_secret": ch["client_secret"],
        "refresh_token": ch["refresh_token"],
    })
    r.raise_for_status()
    data = r.json()

    # Actualizar en memoria y en config.json
    ch["access_token"] = data["access_token"]
    ch["refresh_token"] = data["refresh_token"]
    ch["token_expires_at"] = now_ts + data["expires_in"]
    with open(ROOT / "config.json", "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    return ch["access_token"]

# ── Sync Mercado Libre ────────────────────────────────────────────────────────

def sync_ml(canal_key: str, conn):
    """Importa pedidos 'paid' y 'pending' de un canal ML."""
    ch = CHANNELS[canal_key]
    user_id = ch["user_id"]
    token = refresh_ml_token(canal_key)
    headers = {"Authorization": f"Bearer {token}"}

    print(f"\n[ML] Sincronizando {canal_key}...")

    # Solo pedidos de hoy
    desde = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

    params = {
        "seller": user_id,
        "order.status": "paid",
        "order.date_created.from": desde,
        "sort": "date_desc",
        "limit": 50,
        "offset": 0,
    }
    url = "https://api.mercadolibre.com/orders/search"
    nuevos = 0

    while True:
        r = requests.get(url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        if not results:
            break

        for order in results:
            # Saltar Full ML (se gestiona aparte)
            tags = order.get("tags", []) or []
            if "fulfillment" in tags:
                continue

            external_id = str(order["id"])
            canal_id = canal_key
            numero_pedido = str(order.get("id", ""))
            fecha_compra = order.get("date_created")
            sla_raw = order.get("shipping", {}) or {}
            sla_deadline = sla_raw.get("date_limit_to_handle")

            # Mapear estado real de ML → estado interno
            shipping_status = sla_raw.get("status", "")
            if shipping_status in ("shipped", "delivered", "not_delivered"):
                estado_interno = "despachado"
            elif shipping_status == "ready_to_ship":
                estado_interno = "listo_despacho"
            else:
                estado_interno = "pendiente"

            # Saltar pedidos ya despachados/entregados
            if estado_interno == "despachado":
                continue

            # Cliente
            buyer = order.get("buyer", {}) or {}
            cliente_nombre = buyer.get("nickname", "")

            # Dirección
            shipping = order.get("shipping", {}) or {}
            direccion = shipping.get("receiver_address") or {}

            correo_id = mapear_correo_ml(order)

            # Items
            items_raw = order.get("order_items", [])
            items = []
            for it in items_raw:
                item = it.get("item", {}) or {}
                items.append({
                    "sku": item.get("seller_sku") or item.get("id"),
                    "nombre_producto": item.get("title", ""),
                    "cantidad": it.get("quantity", 1),
                    "imagen_url": None,
                })

            upsert_pedido(conn, {
                "canal_id": canal_id,
                "external_id": external_id,
                "numero_pedido": numero_pedido,
                "correo_id": correo_id,
                "shipping_type_raw": shipping.get("shipping_mode", ""),
                "cliente_nombre": cliente_nombre,
                "cliente_email": None,
                "direccion": json.dumps(direccion),
                "fecha_compra": fecha_compra,
                "sla_deadline": sla_deadline,
                "estado_inicial": estado_interno,
                "raw_payload": json.dumps(order),
                "items": items,
            })
            nuevos += 1

        # Paginación
        paging = data.get("paging", {})
        params["offset"] += params["limit"]
        if params["offset"] >= paging.get("total", 0):
            break

    print(f"  → {nuevos} pedidos procesados")

# ── Sync Tienda Nube ──────────────────────────────────────────────────────────

def sync_tn(canal_key: str, conn):
    """Importa pedidos pendientes de un canal TN."""
    ch = CHANNELS[canal_key]
    store_id = ch["store_id"]
    token = ch["access_token"]
    headers = {
        "Authentication": f"bearer {token}",
        "User-Agent": "LogisticaOMS/1.0 (matiaslerer@gmail.com)",
    }

    print(f"\n[TN] Sincronizando {canal_key}...")

    url = f"https://api.tiendanube.com/v1/{store_id}/orders"
    desde_tn = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
    params = {
        "payment_status": "paid",
        "fulfillment_status": "unshipped",  # Solo los no despachados
        "created_at_min": desde_tn,
        "per_page": 50,
        "page": 1,
    }
    nuevos = 0

    while True:
        r = requests.get(url, headers=headers, params=params)
        if r.status_code == 429:
            print("  Rate limit TN, esperando 10s...")
            import time; time.sleep(10)
            continue
        r.raise_for_status()
        results = r.json()
        if not results:
            break

        # Reconectar si la conexión se cayó
        if conn.closed:
            conn = psycopg2.connect(DB_URL)

        for order in results:
            # Mapear estado TN → estado interno
            fulfillment = order.get("fulfillment_status") or ""
            if fulfillment in ("shipped", "delivered", "fulfilled"):
                continue  # Saltar los ya despachados
            elif fulfillment == "partial":
                estado_interno = "listo_despacho"
            else:
                estado_interno = "pendiente"

            external_id = str(order["id"])
            numero_pedido = str(order.get("number", ""))
            fecha_compra = order.get("created_at")

            # Cliente
            customer = order.get("customer") or {}
            cliente_nombre = f"{customer.get('name', '')}".strip()
            cliente_email = customer.get("email", "")

            # Shipping
            shipping_raw = order.get("shipping") or {}
            correo_id = mapear_correo_tn(order)
            direccion = order.get("shipping_address") or {}

            # Items
            items = []
            for it in (order.get("products") or []):
                items.append({
                    "sku": it.get("sku") or str(it.get("product_id", "")),
                    "nombre_producto": it.get("name", ""),
                    "cantidad": it.get("quantity", 1),
                    "imagen_url": (it.get("image") or {}).get("src"),
                })

            upsert_pedido(conn, {
                "canal_id": canal_key,
                "external_id": external_id,
                "numero_pedido": numero_pedido,
                "correo_id": correo_id,
                "shipping_type_raw": order.get("shipping_option") or "",
                "cliente_nombre": cliente_nombre,
                "cliente_email": cliente_email,
                "direccion": json.dumps(direccion),
                "fecha_compra": fecha_compra,
                "sla_deadline": None,
                "estado_inicial": estado_interno,
                "raw_payload": json.dumps(order),
                "items": items,
            })
            nuevos += 1

        params["page"] += 1
        if len(results) < params["per_page"]:
            break

    print(f"  → {nuevos} pedidos procesados")

# ── Upsert a la DB ────────────────────────────────────────────────────────────

def upsert_pedido(conn, data: dict):
    """Inserta o actualiza un pedido. No toca el estado si ya existe."""
    cur = conn.cursor()

    # Upsert del pedido (no actualiza estado ni asignación si ya existe)
    estado_inicial = data.get("estado_inicial", "pendiente")

    cur.execute("""
        INSERT INTO log_pedidos (
            canal_id, external_id, numero_pedido, correo_id, shipping_type_raw,
            cliente_nombre, cliente_email, direccion,
            fecha_compra, sla_deadline, estado, raw_payload, last_synced_at
        ) VALUES (
            %(canal_id)s, %(external_id)s, %(numero_pedido)s, %(correo_id)s, %(shipping_type_raw)s,
            %(cliente_nombre)s, %(cliente_email)s, %(direccion)s::jsonb,
            %(fecha_compra)s, %(sla_deadline)s, %(estado_inicial)s, %(raw_payload)s::jsonb, now()
        )
        ON CONFLICT (canal_id, external_id) DO UPDATE SET
            correo_id        = EXCLUDED.correo_id,
            shipping_type_raw= EXCLUDED.shipping_type_raw,
            sla_deadline     = EXCLUDED.sla_deadline,
            raw_payload      = EXCLUDED.raw_payload,
            last_synced_at   = now()
        RETURNING id, (xmax = 0) AS inserted
    """, {**data, "estado_inicial": estado_inicial})

    row = cur.fetchone()
    pedido_id, inserted = row

    if inserted:
        for item in data.get("items", []):
            cur.execute("""
                INSERT INTO log_pedido_items (pedido_id, sku, nombre_producto, cantidad, imagen_url)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (pedido_id, item["sku"], item["nombre_producto"], item["cantidad"], item["imagen_url"]))

        cur.execute("""
            INSERT INTO log_pedido_historial (pedido_id, estado_anterior, estado_nuevo, notas)
            VALUES (%s, NULL, %s, 'Importado automáticamente')
        """, (pedido_id, estado_inicial))

    conn.commit()
    cur.close()

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== SYNC PEDIDOS LOGÍSTICA ===")
    print(f"Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ML — una conexión por canal
    for key in ["ml_pret", "ml_lavan"]:
        if CHANNELS.get(key, {}).get("enabled"):
            conn = psycopg2.connect(DB_URL)
            try:
                sync_ml(key, conn)
            except Exception as e:
                print(f"  ⚠️  Error en {key}: {e}")
            finally:
                conn.close()

    # TN — una conexión por canal
    for key in ["tn_pret", "tn_lavan"]:
        if CHANNELS.get(key, {}).get("enabled"):
            conn = psycopg2.connect(DB_URL)
            try:
                sync_tn(key, conn)
            except Exception as e:
                print(f"  ⚠️  Error en {key}: {e}")
            finally:
                conn.close()

    print("\n✅ Sync completado")

if __name__ == "__main__":
    main()
