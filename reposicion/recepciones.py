#!/usr/bin/env python3
"""
reposicion/recepciones.py — detección automática de mercadería recibida.

Lo llama daily_stock.py al final de la corrida diaria (después del sync de
ventas). Decide cuántas unidades de cada línea de pedido (repo_pedido_items)
ya llegaron, y cierra las líneas completas.

Cómo se estima (por SKU, no por destino):

  ingresos desde el pedido = (stock hoy − stock cuando se hizo el pedido)
                             + unidades vendidas desde entonces

  donde "stock" es depósito + Full Pret JUNTOS. Dos razones:
  - La compara contra el stock de ese momento tapaba los ingresos de los
    productos que se venden rápido: llegan 100, se venden 100 y el stock no
    se mueve (el sistema anterior decía "0 recibido").
  - Un pedido "para Full" puede entrar primero al depósito y después
    moverse a Full (regla del usuario). Mirando el pool junto, ese traslado
    interno no cambia el total y no confunde la detección.

Sesgo a propósito CONSERVADOR (pedido del usuario, 2026-10-16: "prefiero que
me sobrealerte a volver a pedir y que el proveedor me avise que ya hay un
pedido en camino"): ante la duda la línea queda abierta, no cerrada. Por eso:
  - no se cuentan ventas de ml_lavan (no se sabe si salieron de Full Lavan,
    que es otro pool, o del depósito) ni las del día de la foto base;
  - lo que está en tránsito entre depósito y Full resta ingresos hasta que
    llega;
  - lo recibido nunca baja una vez registrado;
  - si hay varios pedidos abiertos del mismo SKU, los ingresos se reparten en
    orden (el más viejo primero) y un mismo ingreso no cierra dos pedidos;
  - los pedidos en bultos no se tocan (no se sabe cuántas unidades tiene un
    bulto); un SKU con un pedido en bultos abierto se saltea entero.
"""

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from reposicion import core, db

# Ventas que descuentan del pool depósito + Full Pret. ml_lavan queda afuera
# a propósito (ver docstring del módulo).
CANALES_VENTAS_POOL = ("tn_pret", "tn_lavan", "ml_pret")
ORDEN_DESTINO = {"deposito": 0, "full_pret": 1}
CHUNK_SKUS = 80


def _parse_ts(valor):
    """timestamptz de PostgREST → datetime. Postgres recorta ceros de las
    fracciones (".12345"), y fromisoformat de Python < 3.11 exige 3 o 6
    dígitos — se normaliza a 6."""
    if isinstance(valor, datetime):
        return valor
    s = valor.replace("Z", "+00:00")
    s = re.sub(r"\.(\d+)", lambda m: "." + m.group(1).ljust(6, "0")[:6], s)
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _in_list(valores):
    """Valor de filtro PostgREST `in.(...)` con cada elemento entre comillas
    (los SKU pueden tener comas, espacios o paréntesis)."""
    esc = [str(v).replace("\\", "\\\\").replace('"', '\\"') for v in valores]
    return "in.(" + ",".join(f'"{v}"' for v in esc) + ")"


def estimar_ingreso(stock_hoy_total, snaps, ventas, creado_at):
    """Unidades que entraron al pool (depósito + Full Pret) desde `creado_at`.

    snaps: [{'fecha', 'captured_at', 'total'}] ordenadas por captured_at.
    ventas: [(fecha_iso, cantidad)] de los canales que descuentan del pool.
    Devuelve None si no hay ninguna foto de stock anterior al pedido — sin
    punto de partida no hay evidencia y la línea no se toca."""
    base = None
    for s in snaps:
        if s["captured_at"] <= creado_at:
            base = s
        else:
            break
    if base is None:
        return None
    # Ventas ESTRICTAMENTE posteriores al día de la foto base: las de ese día
    # ya están en parte reflejadas en esa foto — contarlas todas inflaría el
    # ingreso (y cerraría líneas de más).
    vendido = sum(c for f, c in ventas if f > base["fecha"])
    return (stock_hoy_total - base["total"]) + vendido


def calcular_recepciones(items_por_sku, pedidos, snaps_por_sku, ventas_por_sku, stock_hoy):
    """Función pura (sin base de datos) — devuelve la lista de cambios:
    [{'item_id', 'pedido_id', 'sku', 'destino', 'antes', 'ahora', 'pedida', 'completo'}].

    items_por_sku: {sku: [item]} con TODAS las líneas de pedidos no cancelados
      de ese SKU, abiertas o cerradas (las cerradas "consumen" ingresos).
    pedidos: {id: {'creado_at': datetime, 'unidad_medida': str}}.
    stock_hoy: {sku: total depósito + Full Pret hoy}.
    """
    cambios = []
    for sku, items in items_por_sku.items():
        if not any(not i["recibido_completo"] for i in items):
            continue
        hoy = stock_hoy.get(sku)
        if hoy is None:
            continue

        por_pedido = defaultdict(list)
        for i in items:
            por_pedido[i["pedido_id"]].append(i)

        # Desde el pedido abierto más viejo: los anteriores ya están todos
        # cerrados y no compiten por estos ingresos.
        t_min = min(pedidos[pid]["creado_at"] for pid, ls in por_pedido.items()
                    if any(not l["recibido_completo"] for l in ls))
        incluidos = sorted(
            (pid for pid in por_pedido if pedidos[pid]["creado_at"] >= t_min),
            key=lambda pid: (pedidos[pid]["creado_at"], pid),
        )
        if any(pedidos[pid]["unidad_medida"] == "bultos" for pid in incluidos):
            continue

        snaps = snaps_por_sku.get(sku, [])
        ventas = ventas_por_sku.get(sku, [])
        ingresos = {pid: estimar_ingreso(hoy, snaps, ventas, pedidos[pid]["creado_at"]) for pid in incluidos}
        if ingresos[incluidos[0]] is None:
            continue
        disponible = max(0, ingresos[incluidos[0]])
        consumido = 0

        for pid in incluidos:
            lineas = por_pedido[pid]
            pedida = sum(l["cantidad_pedida"] for l in lineas)
            if all(l["recibido_completo"] for l in lineas):
                consumido += pedida  # ya cerrado: se supone recibido entero
                continue
            ya = sum(l["cantidad_recibida"] or 0 for l in lineas)
            propio = max(0, ingresos[pid] or 0)  # ingresos posteriores a ESTE pedido
            asignable = max(0, min(disponible - consumido, propio))
            alloc = min(pedida, max(asignable, ya))  # lo recibido nunca baja
            consumido += alloc

            restante = alloc
            for l in sorted(lineas, key=lambda l: (ORDEN_DESTINO.get(l["destino"], 9), l["id"])):
                parte = min(l["cantidad_pedida"], restante)
                restante -= parte
                actual = l["cantidad_recibida"] or 0
                if l["recibido_completo"]:
                    continue
                nuevo = max(actual, parte)
                completo = nuevo >= l["cantidad_pedida"]
                if nuevo != actual or completo:
                    cambios.append({
                        "item_id": l["id"], "pedido_id": pid, "sku": sku, "destino": l["destino"],
                        "antes": actual, "ahora": nuevo, "pedida": l["cantidad_pedida"], "completo": completo,
                    })
    return cambios


# ─── Lectura / escritura ───────────────────────────────────────────────────

def _cargar(config, stock_hoy_by_sku):
    pedidos_rows = db.select(config, "repo_pedidos", params={
        "select": "id,creado_at,unidad_medida", "estado": "neq.cancelado", "order": "id",
    })
    pedidos = {p["id"]: {"creado_at": _parse_ts(p["creado_at"]), "unidad_medida": p.get("unidad_medida") or "unidades"}
               for p in pedidos_rows}
    if not pedidos:
        return {}, pedidos, {}, {}, {}

    abiertos = db.select(config, "repo_pedido_items", params={
        "select": "sku,pedido_id", "recibido_completo": "eq.false", "order": "id",
    })
    skus = sorted({i["sku"] for i in abiertos if i["pedido_id"] in pedidos})
    if not skus:
        return {}, pedidos, {}, {}, {}

    t_min = min(p["creado_at"] for p in pedidos.values())
    desde = (t_min - timedelta(days=3)).date().isoformat()

    items_por_sku, snaps_por_sku, ventas_por_sku = defaultdict(list), defaultdict(list), defaultdict(list)
    for k in range(0, len(skus), CHUNK_SKUS):
        chunk = skus[k:k + CHUNK_SKUS]
        for it in db.select(config, "repo_pedido_items", params={
            "select": "id,pedido_id,sku,destino,cantidad_pedida,cantidad_recibida,recibido_completo",
            "sku": _in_list(chunk), "order": "id",
        }):
            if it["pedido_id"] in pedidos:
                items_por_sku[it["sku"]].append(it)
        for s in db.select(config, "repo_stock_snapshot", params={
            "select": "sku,fecha,stock_deposito,stock_full_pret,captured_at",
            "sku": _in_list(chunk), "fecha": f"gte.{desde}", "order": "sku,fecha",
        }):
            snaps_por_sku[s["sku"]].append({
                "fecha": s["fecha"], "captured_at": _parse_ts(s["captured_at"]),
                "total": (s["stock_deposito"] or 0) + (s["stock_full_pret"] or 0),
            })
        for v in db.select(config, "repo_ventas_items", params={
            "select": "sku,fecha,cantidad", "sku": _in_list(chunk), "fecha": f"gte.{desde}",
            "canal": "in.(" + ",".join(CANALES_VENTAS_POOL) + ")", "estado": "eq.activa", "order": "id",
        }):
            ventas_por_sku[v["sku"]].append((v["fecha"], v["cantidad"] or 0))
    for lista in snaps_por_sku.values():
        lista.sort(key=lambda s: s["captured_at"])

    stock_hoy = {
        sku: (stock_hoy_by_sku[sku].get("deposito", 0) or 0) + (stock_hoy_by_sku[sku].get("full_pret", 0) or 0)
        for sku in skus if sku in stock_hoy_by_sku
    }
    return items_por_sku, pedidos, snaps_por_sku, ventas_por_sku, stock_hoy


def _actualizar_estado_pedidos(config, pedido_ids):
    for pedido_id in pedido_ids:
        items = db.select(config, "repo_pedido_items", params={
            "select": "recibido_completo,cantidad_recibida", "pedido_id": f"eq.{pedido_id}",
        })
        if all(i["recibido_completo"] for i in items):
            nuevo_estado = "recibido_completo"
        elif any(i["recibido_completo"] or (i["cantidad_recibida"] or 0) > 0 for i in items):
            nuevo_estado = "recibido_parcial"
        else:
            continue
        db.patch(config, "repo_pedidos", params={"id": f"eq.{pedido_id}"}, body={"estado": nuevo_estado})


def reconciliar_pedidos(config, stock_hoy_by_sku, dry_run=False):
    """stock_hoy_by_sku: {sku: {'deposito': int, 'full_pret': int, ...}}.
    Con dry_run=True no escribe nada y devuelve los cambios que haría."""
    items_por_sku, pedidos, snaps, ventas, stock_hoy = _cargar(config, stock_hoy_by_sku)
    cambios = calcular_recepciones(items_por_sku, pedidos, snaps, ventas, stock_hoy)
    if dry_run:
        return cambios

    ahora = datetime.now(timezone.utc).isoformat()
    for c in cambios:
        body = {"cantidad_recibida": c["ahora"]}
        if c["completo"]:
            body.update(recibido_completo=True, recibido_at=ahora)
        db.patch(config, "repo_pedido_items", params={"id": f"eq.{c['item_id']}"}, body=body)
    _actualizar_estado_pedidos(config, {c["pedido_id"] for c in cambios})

    cerradas = sum(1 for c in cambios if c["completo"])
    core.tnlog(f"  Recepciones: {len(cambios)} líneas actualizadas ({cerradas} cerradas) "
               f"en {len({c['pedido_id'] for c in cambios})} pedidos")
    return cambios
