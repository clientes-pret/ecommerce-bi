#!/usr/bin/env python3
"""
reposicion/scorecard_ml_sync.py — Scorecard de Mercado Libre (Pret y Laván) directo desde las APIs de ML y
MercadoPago, con la misma lógica que la planilla "RENTABILIDADES MERCADO LIBRE" (análisis de rentabilidad).

Por cada día y cuenta calcula y guarda en Supabase con scorecard_guardar_dia() (mismas claves que la rutina):
- ventas y órdenes   = monto de las órdenes del día (FAC. BRUTA). Reglas verificadas contra la exportación
                       de la planilla del 5/11/2026 (8 días, ~2.200 órdenes, el mismo conjunto salvo 2):
                       · el día es el de CIERRE de la orden (date_closed) en hora de Argentina, no el de creación;
                       · cuentan las órdenes no canceladas con algún pago aprobado (las que tienen el pago
                         "en mediación" o rechazado no entran).
- facturación neta   = MONTO MP: lo que realmente entra a MercadoPago por esas órdenes (net_received_amount
                       de cada pago, repartido por el valor de la orden cuando un pago cubre un carrito).
                       La planilla suma además una "bonificación por envío" de Flex (~0,1 % del neto) que acá
                       no se incluye.
- costo de productos = costo de Tiendanube de cada SKU (con IVA) × cantidad: primero el catálogo de la
                       propia marca, después el de la otra, y por último la tabla scorecard_costos_extra
                       (SKUs que ya no están en Tiendanube, cargados de la planilla).
- inversión en ML Ads = Product Ads + Display/Brand Ads (API de publicidad de ML; coincide con el panel "Publicidad"),
                       clave ml_ads_pret / ml_ads_lavan.
                       Un día sin campañas con gasto es 0, no "pendiente".
Si falta algún pago de MercadoPago de un día, ese día NO guarda la neta (queda lo que había) y avisa.

Los últimos días se recalculan cada vez (devoluciones y reclamos cambian días ya cargados).

Uso: python3 -m reposicion.scorecard_ml_sync                       (simula: últimos 4 días, no guarda)
     python3 -m reposicion.scorecard_ml_sync --aplicar             (guarda)
     python3 -m reposicion.scorecard_ml_sync --desde 2026-10-06 --aplicar   (carga histórica)
Pruebas: python3 -m reposicion.tests.test_scorecard_ml
"""

import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests

from reposicion import core, db
from reposicion.scorecard_tn_sync import guardar_dia

AR = timezone(timedelta(hours=-3))
MP = "https://api.mercadopago.com"
CUENTAS = {  # canal del config -> (clave del documento del Scorecard, canal TN propio, canal TN de respaldo)
    "ml_pret": ("ml_pret_home", "tn_pret", "tn_lavan"),
    "ml_lavan": ("ml_casa_lavan", "tn_lavan", "tn_pret"),
}
ADVERTISERS = {"ml_pret": (27450, "ml_ads_pret"), "ml_lavan": (1144462, "ml_ads_lavan")}  # ids estables de Product Ads


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def dia_cierre(o):
    """Día (Argentina) en que cerró la orden; es el 'Fecha de venta' de la planilla."""
    f = o.get("date_closed") or o.get("date_created")
    return datetime.fromisoformat(f).astimezone(AR).date()


def cuenta(o):
    """Una orden entra si no está cancelada y tiene un pago aprobado."""
    return o.get("status") != "cancelled" and any(p.get("status") == "approved" for p in o.get("payments") or [])


def traer_ordenes(key, cfg, desde, hasta):
    """Órdenes creadas desde 5 días antes de `desde` (una orden se cierra días después de crearse) hasta `hasta`."""
    token = core.ml_ensure_token(key, cfg)
    ini = datetime.combine(desde - timedelta(days=5), datetime.min.time(), AR)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), AR)
    ordenes, vistas = [], set()
    tramo = ini
    while tramo < fin:                                   # tramos de 3 días: ML no deja pasar de 10.000 por consulta
        tope = min(tramo + timedelta(days=3), fin)
        offset = 0
        while True:
            d = core.ml_get("https://api.mercadolibre.com/orders/search", token, params={
                "seller": cfg["user_id"], "sort": "date_asc", "offset": offset, "limit": 50,
                "order.date_created.from": tramo.isoformat(timespec="milliseconds"),
                "order.date_created.to": tope.isoformat(timespec="milliseconds")})
            if not d or not d.get("results"):
                break
            for o in d["results"]:
                if o["id"] not in vistas:
                    vistas.add(o["id"])
                    ordenes.append(o)
            offset += 50
            if offset >= d.get("paging", {}).get("total", 0):
                break
            time.sleep(0.2)
        tramo = tope
    return ordenes


def _pago_neto(token, pid):
    for intento in range(4):
        j = core.ml_get(f"{MP}/v1/payments/{pid}", token)
        if j:
            return pid, _num((j.get("transaction_details") or {}).get("net_received_amount")), _num(j.get("transaction_amount"))
        time.sleep(2 * (intento + 1))
    return pid, None, None


def traer_pagos(token, ordenes):
    """{id de pago: (neto recibido, monto de la transacción)} de los pagos aprobados; None = no se pudo leer."""
    ids = sorted({p["id"] for o in ordenes if cuenta(o) for p in o.get("payments") or [] if p.get("status") == "approved"})
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(lambda pid: _pago_neto(token, pid), ids))
    return {pid: (net, monto) for pid, net, monto in res}


def sku_de(item):
    it = item.get("item") or {}
    return str(it.get("seller_sku") or it.get("seller_custom_field") or "").strip()


def _get_ads(url, token, params=None):
    """GET a la API de publicidad de ML (header api-version 2). Reintenta 429/5xx; si falla lanza error."""
    for intento in range(4):
        r = requests.get(url, headers={"Authorization": f"Bearer {token}", "api-version": "2"}, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503):
            time.sleep(3 * (intento + 1))
            continue
        break
    raise RuntimeError(f"ML Ads {url.split('/advertising/')[-1]}: {r.status_code} {r.text[:200]}")


def traer_ads(token, advertiser, desde, hasta):
    """Inversión en publicidad de ML por día (str YYYY-MM-DD -> pesos) entre desde y hasta = lo que muestra el panel
    "Publicidad" de ML: Product Ads (costo de las campañas de productos) + Display y Brand Ads (consumed_budget de cada
    campaña). Verificado el 10/11/2026 contra el panel de Pret (30 días): 20.289.908 + 7.216.325 = 27.506.233 vs 27.505.645.
    Los días sin gasto valen 0. Si cualquier consulta falla lanza error: nunca se guarda un total incompleto."""
    base = "https://api.mercadolibre.com/advertising"
    rango = {"date_from": desde.isoformat(), "date_to": hasta.isoformat()}
    total = {}
    prod = _get_ads(f"{base}/MLA/advertisers/{advertiser}/product_ads/campaigns/search", token,
                    {"limit": 1, "offset": 0, "metrics": "cost", "aggregation_type": "DAILY", **rango})
    for x in prod.get("results", []):
        if x.get("date"):
            total[x["date"]] = total.get(x["date"], 0.0) + _num(x.get("cost"))
    try:
        camp = _get_ads(f"{base}/advertisers/{advertiser}/display/campaigns", token)
    except RuntimeError as e:
        if ": 401 " not in str(e):
            raise
        camp = {}   # la cuenta no tiene el producto Display (la API responde "sin acceso"): solo cuenta Product Ads
    for c in camp.get("results", []):
        m = _get_ads(f"{base}/advertisers/{advertiser}/display/campaigns/{c['campaign_id']}/metrics", token, rango)
        for x in m.get("metrics", []):
            if x.get("date"):
                total[x["date"]] = total.get(x["date"], 0.0) + _num(x.get("consumed_budget"))
    dias = (hasta - desde).days + 1
    fechas = [(desde + timedelta(days=i)).isoformat() for i in range(dias)]
    return {f: round(total.get(f, 0.0), 2) for f in fechas}


def calcular(ordenes, pagos, costos, desde, hasta):
    """Totales por día. Devuelve ({fecha ISO: {orders, revenue, neta, costo, neta_completa}}, avisos)."""
    dias = {}
    d = desde
    while d <= hasta:
        dias[d.isoformat()] = {"orders": 0, "revenue": 0.0, "neta": 0.0, "costo": 0.0, "neta_completa": True}
        d += timedelta(days=1)
    avisos = {"pagos_sin_dato": 0, "skus_sin_costo": {}}
    for o in ordenes:
        if not cuenta(o):
            continue
        dia = dia_cierre(o).isoformat()
        if dia not in dias:
            continue
        t = dias[dia]
        total = _num(o.get("total_amount"))
        t["orders"] += 1
        t["revenue"] += total
        for it in o.get("order_items") or []:
            sku, cant = sku_de(it), _num(it.get("quantity"))
            if sku in costos:
                t["costo"] += costos[sku] * cant
            else:
                avisos["skus_sin_costo"][sku] = avisos["skus_sin_costo"].get(sku, 0) + cant
        for p in o.get("payments") or []:
            if p.get("status") != "approved":
                continue
            net, monto = pagos.get(p["id"], (None, None))
            if net is None:
                avisos["pagos_sin_dato"] += 1
                t["neta_completa"] = False
                continue
            # un pago de carrito cubre varias órdenes: a esta le toca lo que vale respecto del pago
            t["neta"] += net * min(total / monto, 1.0) if monto else net
    return dias, avisos


def _productos_tn(cfg):
    """Catálogo de Tiendanube; a veces da timeout, se reintenta antes de rendirse."""
    for intento in range(4):
        try:
            return core.tn_get_products(cfg)
        except Exception as e:
            core.tnlog(f"  ⚠ catálogo TN {cfg.get('label')}: {e} (intento {intento + 1}/4)")
            time.sleep(10 * (intento + 1))
    raise RuntimeError(f"No se pudo leer el catálogo de Tiendanube de {cfg.get('label')}")


def costos_por_sku(config, key):
    """Costo con IVA por SKU: catálogo TN de la marca, después el de la otra y por último scorecard_costos_extra."""
    _, propio, respaldo = CUENTAS[key]
    costos = {}
    for tn in (propio, respaldo):
        cfg = config["channels"].get(tn)
        if not cfg:
            continue
        for p in _productos_tn(cfg):
            for v in p.get("variants") or []:
                sku = (v.get("sku") or "").strip()
                if sku and sku not in costos and _num(v.get("cost")) > 0:
                    costos[sku] = _num(v["cost"])
    for f in db.select(config, "scorecard_costos_extra", {"select": "sku,costo", "order": "sku"}):
        costos.setdefault(f["sku"], _num(f["costo"]))
    return costos


def documento(clave, t):
    """Campos del día con las mismas claves que usa la rutina del Scorecard."""
    doc = {
        clave: {"orders": t["orders"], "revenue": round(t["revenue"], 2)},
        f"costo_productos_{clave}": round(t["costo"], 2),
    }
    if t["neta_completa"]:
        doc[f"facturacion_neta_{clave}"] = round(t["neta"], 2)
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", help="YYYY-MM-DD (por defecto, los últimos --dias días hasta ayer)")
    ap.add_argument("--dias", type=int, default=4)
    ap.add_argument("--aplicar", action="store_true", help="guardar en Supabase (sin esto solo muestra)")
    args = ap.parse_args()
    config = db.load_config()
    hasta = datetime.now(AR).date() - timedelta(days=1)       # el día de hoy está a medias: se carga mañana
    desde = date.fromisoformat(args.desde) if args.desde else hasta - timedelta(days=args.dias - 1)
    errores = []
    for key, (clave, _, _) in CUENTAS.items():
        cfg = config["channels"].get(key, {})
        if not cfg.get("enabled", True):
            continue
        try:
            ordenes = traer_ordenes(key, cfg, desde, hasta)
            token = core.ml_ensure_token(key, cfg)
            pagos = traer_pagos(token, [o for o in ordenes if desde <= dia_cierre(o) <= hasta])
            dias, avisos = calcular(ordenes, pagos, costos_por_sku(config, key), desde, hasta)
            for fecha, t in sorted(dias.items()):
                core.tnlog(f"{key} {fecha}: {t['orders']} órdenes · ventas {t['revenue']:,.0f} · neta {t['neta']:,.0f}"
                           f"{'' if t['neta_completa'] else ' (INCOMPLETA, no se guarda)'} · costo {t['costo']:,.0f}")
                if args.aplicar:
                    guardar_dia(config, fecha, documento(clave, t))
            sin = avisos["skus_sin_costo"]
            core.tnlog(f"  ✓ {cfg.get('label', key)}: {len(dias)} días {'guardados' if args.aplicar else '(simulación, no se guardó)'}"
                       + (f" · ⚠ {avisos['pagos_sin_dato']} pagos de MercadoPago sin leer" if avisos["pagos_sin_dato"] else "")
                       + (f" · ⚠ {len(sin)} SKUs sin costo ({sum(sin.values()):.0f} u.): "
                          + ", ".join(sorted(sin, key=sin.get, reverse=True)[:8]) if sin else ""))
        except Exception as e:
            errores.append(f"{key}: {e}")
            core.tnlog(f"  ⚠ {key}: {e}")
        try:                                                     # la pauta va aparte: si falla no frena las ventas
            adv, clave_ads = ADVERTISERS[key]
            ads = traer_ads(core.ml_ensure_token(key, cfg), adv, desde, hasta)
            for fecha, costo in sorted(ads.items()):
                if args.aplicar:
                    guardar_dia(config, fecha, {clave_ads: costo})
            core.tnlog(f"  ✓ ML Ads {cfg.get('label', key)}: " + " · ".join(f"{f[5:]} {c:,.0f}" for f, c in sorted(ads.items()))
                       + ("" if args.aplicar else " (simulación, no se guardó)"))
        except Exception as e:
            errores.append(f"{key} ads: {e}")
            core.tnlog(f"  ⚠ {key} ads: {e}")
    if errores:
        raise SystemExit("Terminó con errores:\n" + "\n".join(errores))


if __name__ == "__main__":
    main()
