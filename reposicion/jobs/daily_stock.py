#!/usr/bin/env python3
"""
reposicion/jobs/daily_stock.py — corre 1 vez por día (GitHub Actions cron).

1) Baja stock actual: depósito (TN Pret, única fuente — ver core.py) y Full
   (ML Pret / ML Lavan, separados) usando paginación scroll (sin techo).
2) Upsertea repo_stock_snapshot para la fecha de hoy.
3) Compara contra el snapshot anterior: abre/cierra filas en
   repo_quiebre_historial cuando un SKU/destino pasa a 0 o vuelve a tener stock.
4) Reconcilia pedidos abiertos (repo_pedido_items con recibido_completo=false)
   contra el stock de hoy — marca recepción completa/parcial.

Uso local: python3 -m reposicion.jobs.daily_stock
"""

import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reposicion import core, db, ventas_sync

DESTINOS = ("deposito", "full_pret", "full_lavan")
DESTINO_A_COLUMNA = {
    "deposito":   "stock_deposito",
    "full_pret":  "stock_full_pret",
    "full_lavan": "stock_full_lavan",
}


def fetch_stock(config):
    """Devuelve ({sku: {'deposito': int, 'full_pret': int, 'full_lavan': int}},
    {'ml_pret': item_details, 'ml_lavan': item_details}) — el segundo valor se
    reusa para sincronizar ventas sin volver a pedir el catálogo ML ese mismo
    día (ver main())."""
    channels = config["channels"]
    stock = defaultdict(lambda: {"deposito": 0, "full_pret": 0, "full_lavan": 0})
    item_details_by_channel = {}

    # ── Depósito: única fuente es TN Pret (ver generar_reporte.py:755/786) ──
    tn_pret_cfg = channels.get("tn_pret")
    if tn_pret_cfg and tn_pret_cfg.get("enabled", True):
        core.tnlog("→ TN Pret: bajando stock de depósito...")
        products = core.tn_get_products(tn_pret_cfg)
        for prod in products:
            for variant in prod.get("variants", []):
                sku = str(variant.get("sku") or variant.get("id") or "")
                if not sku:
                    continue
                stock[sku]["deposito"] += variant.get("stock", 0) or 0
        core.tnlog(f"  ✓ {len(products)} productos TN Pret procesados")

    # ── Full: ML Pret y ML Lavan, separados ──
    full_key_by_channel = {"ml_pret": "full_pret", "ml_lavan": "full_lavan"}
    for chan_key, destino in full_key_by_channel.items():
        cfg = channels.get(chan_key)
        if not cfg or not cfg.get("enabled", True):
            continue
        core.tnlog(f"→ {cfg['label']}: bajando stock Full (scroll)...")
        token = core.ml_ensure_token(chan_key, cfg)
        item_ids = core.ml_scroll_item_ids(token, cfg["user_id"])
        details = core.ml_items_by_ids(token, item_ids)
        item_details_by_channel[chan_key] = details
        stock_full, _stock_nofull = core.ml_stock_by_sku(details)
        for sku, qty in stock_full.items():
            stock[sku][destino] += qty
        core.tnlog(f"  ✓ {cfg['label']}: {len(stock_full)} SKUs con stock Full")

    return stock, item_details_by_channel


def upsert_productos(config, skus):
    """Upsert 'bare' (solo sku) para que la FK de repo_stock_snapshot no falle
    con SKUs nuevos — ignora duplicados, nunca pisa proveedor_manual/descontinuado
    ya seteados por un usuario."""
    rows = [{"sku": sku} for sku in skus]
    db.insert(config, "repo_productos", rows, ignore_duplicates=True)


def upsert_snapshot(config, stock_by_sku, fecha):
    rows = [
        {
            "fecha": fecha,
            "sku": sku,
            "stock_deposito": v["deposito"],
            "stock_full_pret": v["full_pret"],
            "stock_full_lavan": v["full_lavan"],
        }
        for sku, v in stock_by_sku.items()
    ]
    db.upsert(config, "repo_stock_snapshot", rows, on_conflict="fecha,sku")
    return rows


def previous_snapshot_by_sku(config, fecha_hoy):
    """Último snapshot anterior a hoy, uno por SKU (para detectar transiciones)."""
    rows = db.select(config, "repo_stock_snapshot", params={
        "select": "sku,fecha,stock_deposito,stock_full_pret,stock_full_lavan",
        "fecha": f"lt.{fecha_hoy}",
        "order": "sku.asc,fecha.desc",
    })
    prev = {}
    for r in rows:
        if r["sku"] not in prev:  # ya viene ordenado desc por fecha -> primero = más reciente
            prev[r["sku"]] = r
    return prev


def detect_quiebres(config, hoy_rows, prev_by_sku, fecha_hoy):
    nuevas_aperturas = []
    for row in hoy_rows:
        sku = row["sku"]
        prev = prev_by_sku.get(sku)
        if prev is None:
            continue  # sin dato anterior, no podemos comparar transición
        for destino in DESTINOS:
            col = DESTINO_A_COLUMNA[destino]
            stock_prev = prev.get(col, 0) or 0
            stock_hoy = row.get(col, 0) or 0
            if stock_prev > 0 and stock_hoy == 0:
                nuevas_aperturas.append({
                    "sku": sku,
                    "destino": destino,
                    "fecha_quiebre": fecha_hoy,
                    "stock_previo": stock_prev,
                })
            elif stock_prev == 0 and stock_hoy > 0:
                # Cierra el quiebre abierto (si había uno) para este sku/destino
                db.patch(config, "repo_quiebre_historial",
                         params={"sku": f"eq.{sku}", "destino": f"eq.{destino}",
                                 "fecha_reposicion": "is.null"},
                         body={"fecha_reposicion": fecha_hoy})
    if nuevas_aperturas:
        # ignore_duplicates: el índice único parcial evita duplicar un quiebre
        # ya abierto si el job corre más de una vez el mismo día.
        db.insert(config, "repo_quiebre_historial", nuevas_aperturas, ignore_duplicates=True)
    core.tnlog(f"  {len(nuevas_aperturas)} quiebres nuevos detectados")


def reconciliar_pedidos(config, hoy_by_sku, fecha_hoy):
    pendientes = db.select(config, "repo_pedido_items", params={
        "select": "id,pedido_id,sku,destino,cantidad_pedida,stock_al_pedir,cantidad_recibida",
        "recibido_completo": "eq.false",
    })
    if not pendientes:
        return

    # Un pedido cancelado no debe seguir "recibiendo" stock por esta
    # reconciliación automática — si no, un pedido cancelado con líneas aún
    # sin cerrar quedaría marcado como recibido_parcial/completo apenas
    # entrara stock nuevo de ese SKU por cualquier otro motivo.
    cancelados = db.select(config, "repo_pedidos", params={
        "select": "id", "estado": "eq.cancelado",
    })
    ids_cancelados = {p["id"] for p in cancelados}
    pendientes = [i for i in pendientes if i["pedido_id"] not in ids_cancelados]
    if not pendientes:
        return

    pedidos_tocados = set()
    for item in pendientes:
        sku, destino = item["sku"], item["destino"]
        stock_hoy = hoy_by_sku.get(sku, {}).get(destino)
        if stock_hoy is None or item.get("stock_al_pedir") is None:
            continue
        delta = stock_hoy - item["stock_al_pedir"]
        recibido = min(item["cantidad_pedida"], max(0, delta))
        if recibido == item.get("cantidad_recibida", 0):
            continue  # sin cambios desde la última corrida
        body = {"cantidad_recibida": recibido}
        if recibido >= item["cantidad_pedida"]:
            body["recibido_completo"] = True
            body["recibido_at"] = datetime.now(timezone.utc).isoformat()
        db.patch(config, "repo_pedido_items", params={"id": f"eq.{item['id']}"}, body=body)
        pedidos_tocados.add(item["pedido_id"])

    for pedido_id in pedidos_tocados:
        items = db.select(config, "repo_pedido_items", params={
            "select": "recibido_completo,cantidad_recibida",
            "pedido_id": f"eq.{pedido_id}",
        })
        if all(i["recibido_completo"] for i in items):
            nuevo_estado = "recibido_completo"
        elif any(i["recibido_completo"] or i["cantidad_recibida"] > 0 for i in items):
            nuevo_estado = "recibido_parcial"
        else:
            continue
        db.patch(config, "repo_pedidos", params={"id": f"eq.{pedido_id}"}, body={"estado": nuevo_estado})

    core.tnlog(f"  {len(pedidos_tocados)} pedidos con recepción actualizada")


def main():
    config = db.load_config()
    fecha_hoy = date.today().isoformat()

    core.tnlog(f"═══ Stock diario — {fecha_hoy} ═══")
    stock_by_sku, item_details_by_channel = fetch_stock(config)
    core.tnlog(f"  {len(stock_by_sku)} SKUs con stock relevado")

    prev_by_sku = previous_snapshot_by_sku(config, fecha_hoy)

    upsert_productos(config, stock_by_sku.keys())
    hoy_rows = upsert_snapshot(config, stock_by_sku, fecha_hoy)

    detect_quiebres(config, hoy_rows, prev_by_sku, fecha_hoy)
    reconciliar_pedidos(config, stock_by_sku, fecha_hoy)

    core.tnlog("→ Sincronizando ventas incrementales de los 4 canales...")
    for canal in ("tn_pret", "tn_lavan", "ml_pret", "ml_lavan"):
        try:
            ventas_sync.sync_incremental(config, canal, item_details=item_details_by_channel.get(canal))
        except Exception as e:
            core.tnlog(f"  ⚠ Sync de ventas falló para {canal}: {e}")

    core.tnlog("✓ Job diario terminado")


if __name__ == "__main__":
    main()
