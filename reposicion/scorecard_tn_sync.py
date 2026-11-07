#!/usr/bin/env python3
"""
reposicion/scorecard_tn_sync.py — Scorecard de Tiendanube (Pret y Laván) directo desde la API, sin Excel.

Por cada día y tienda calcula lo que el Scorecard necesitaba de Tiendanube y lo guarda en Supabase con
scorecard_guardar_dia() (la misma función que usa la rutina diaria de claude.ai, que mezcla los campos con
lo que ya había ese día):
- ventas y órdenes   = total de las órdenes pagadas (payment_status = 'paid', no canceladas), por día de
                       creación en hora de Argentina. Las que tienen reembolso parcial vienen sin fecha de pago
                       y no cuentan (así lo venía cargando el Scorecard; verificado 19/10/2026 al peso).
- costo de productos = costo de cada producto de la orden × cantidad.
- facturación neta   = lo que queda después de Pago Nube: el neto de cf_pagonube_movimientos (real si se importó
                       el reporte, calculado si no) y, para órdenes que no pasan por Pago Nube (ej. Go Cuotas),
                       el total sin descuento. Por eso corre DESPUÉS de cashflow_sync.
- costo de envío (solo Laván) = misma tarifa que paga Pret según la zona del cliente (CABA $2.800, GBA $4.500 /
                       $6.000 / $7.750; punto de retiro $0), pedido del usuario el 6/11/2026: "idéntico a Pret a Home".
                       La zona sale del código postal, con el tramo que Pret le asignó a ese código en sus órdenes de los
                       últimos 75 días. Los envíos por Correo Argentino/Andreani no se suman (el monto no viene en la API,
                       igual que en Pret): ese día queda subestimado y el log avisa cuántas órdenes faltaron.
                       Envío Nube (Correo Argentino / Andreani, ambas marcas) = la línea "Envío" de la venta
                       (shipping_cost_customer). Pret (desde 7/11/2026): se calcula acá también; ya no depende de la rutina.

Los últimos días se recalculan cada vez (un reembolso o cancelación posterior cambia un día ya cargado).

Uso: python3 -m reposicion.scorecard_tn_sync                       (simula: últimos 7 días, no guarda)
     python3 -m reposicion.scorecard_tn_sync --aplicar             (guarda)
     python3 -m reposicion.scorecard_tn_sync --desde 2026-05-13 --aplicar   (carga histórica)
     python3 -m reposicion.scorecard_tn_sync --solo tn_lavan --desde 2026-10-28 --aplicar   (una sola tienda)
Pruebas: python3 -m reposicion.tests.test_scorecard_tn
"""

import argparse
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

from reposicion import core, db

AR = timezone(timedelta(hours=-3))
# Costo REAL que paga el dueño por cada envío propio de Pret (mismos valores que la rutina diaria, Paso 1c).
TARIFA_PRET = {"Envío en el día - CABA": 2800.0, "Envío en el día - GBA 1": 4500.0,
               "Envío en el día - GBA 2": 6000.0, "Envío en el día - GBA 3": 7750.0}
DIAS_ZONAS = 75                      # historia de Pret para saber a qué tramo pertenece cada código postal
TIENDAS = {  # canal del config -> (tienda de cf_pagonube_movimientos, clave del documento del Scorecard)
    "tn_pret": ("pret", "tn_pret_home"),
    "tn_lavan": ("lavan", "tn_casa_lavan"),
}


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _dia_ar(iso):
    return datetime.fromisoformat(iso.replace("+0000", "+00:00")).astimezone(AR).date()


def traer_ordenes(cfg, desde, hasta):
    """Órdenes creadas entre `desde` y `hasta` (días de Argentina, ambos incluidos)."""
    base = f"https://api.tiendanube.com/v1/{cfg['store_id']}"
    params = {
        "per_page": 200,
        "created_at_min": datetime.combine(desde, datetime.min.time(), AR).isoformat(),
        "created_at_max": datetime.combine(hasta + timedelta(days=1), datetime.min.time(), AR).isoformat(),
    }
    ordenes, page = [], 1
    while True:
        for intento in range(3):
            r = requests.get(f"{base}/orders", headers=core.tn_headers(cfg), timeout=60, params={**params, "page": page})
            if r.status_code in (200, 404):
                break
            time.sleep(5)
        if r.status_code == 404:                        # TN devuelve 404 cuando la página ya no tiene órdenes
            break
        r.raise_for_status()
        lote = r.json()
        ordenes.extend(lote)
        if len(lote) < 200:
            break
        page += 1
        time.sleep(0.5)
    return ordenes


def netos_pagonube(config, tienda, desde):
    """{número de venta: neto} de Pago Nube. Si una venta está real y calculada, gana la real."""
    filas = db.select(config, "cf_pagonube_movimientos", {
        "select": "numero_venta,neto,estimado", "tienda": f"eq.{tienda}", "descripcion": "eq.Venta",
        "fecha_creacion": f"gte.{(desde - timedelta(days=7)).isoformat()}", "order": "clave"})
    netos = {}
    for f in sorted(filas, key=lambda f: not f["estimado"]):   # primero las calculadas, después las reales las pisan
        netos[str(f["numero_venta"])] = _num(f["neto"])
    return netos


def _cp(orden):
    """Código postal de 4 dígitos del envío ('' si no hay)."""
    return "".join(ch for ch in ((orden.get("shipping_address") or {}).get("zipcode") or "") if ch.isdigit())[:4]


def mapa_zonas(ordenes_pret):
    """{código postal: costo de envío} según el tramo que Pret le cobró a cada código (el más frecuente)."""
    votos = defaultdict(lambda: defaultdict(int))
    for o in ordenes_pret:
        if o.get("status") == "cancelled" or o.get("payment_status") != "paid":
            continue
        costo = TARIFA_PRET.get(o.get("shipping_option") or "")
        if costo is not None and _cp(o):
            votos[_cp(o)][costo] += 1
    return {cp: max(v.items(), key=lambda kv: (kv[1], -kv[0]))[0] for cp, v in votos.items()}


def _es_envio_nube(orden):
    return "Envío Nube" in (orden.get("shipping_option") or "") or orden.get("shipping_carrier_name") == "Envío Nube"


def costo_envio(orden, zonas):
    """(costo, motivo) del envío de una orden. motivo: None = ok, 'sin_dato' = envío sin monto en la venta,
    'sin_zona' = código postal que no se pudo ubicar.
    - Envío Nube (Correo Argentino, Andreani, sucursal): lo que dice la línea "Envío" de la venta
      (`shipping_cost_customer`, ej. $9.802 en Correo Clásico a domicilio). Pedido del usuario 7/11/2026.
    - Envío en el día (CABA/GBA): la tarifa real que paga el dueño (TARIFA_PRET), no lo que cobra al cliente.
    - Envío "Gratis" en el día: la tarifa de la zona (el cliente no paga, el dueño sí). Retiro propio: $0."""
    opcion = orden.get("shipping_option") or ""
    if _es_envio_nube(orden):
        return _num(orden.get("shipping_cost_customer")), None
    if opcion == "Punto de retiro" or opcion == "PRET A HOME":
        return 0.0, None
    if opcion in TARIFA_PRET:
        return TARIFA_PRET[opcion], None
    if "Gratis" in opcion:                                  # el cliente no paga, pero el repartidor cobra la tarifa de la zona
        cp = _cp(orden)
        if cp in zonas:
            return zonas[cp], None
        if cp.isdigit() and 1000 <= int(cp) <= 1499:
            return TARIFA_PRET["Envío en el día - CABA"], None
        return 0.0, "sin_zona"
    if "Correo" in opcion or "Andreani" in opcion or "estándar" in opcion or not opcion:
        monto = _num(orden.get("shipping_cost_customer"))
        return (monto, None) if monto else (0.0, "sin_dato")
    cp = _cp(orden)
    if cp in zonas:
        return zonas[cp], None
    if cp.isdigit() and 1000 <= int(cp) <= 1499:            # Capital Federal
        return TARIFA_PRET["Envío en el día - CABA"], None
    return 0.0, "sin_zona"


def calcular(ordenes, netos, desde, hasta, zonas=None):
    """Totales por día. Devuelve ({fecha ISO: {orders, revenue, costo, neta[, envio]}}, avisos).
    Con `zonas` (mapa_zonas de Pret) calcula también el costo de envío."""
    dias = {}
    d = desde
    while d <= hasta:
        dias[d.isoformat()] = {"orders": 0, "revenue": 0.0, "costo": 0.0, "neta": 0.0}
        if zonas is not None:
            dias[d.isoformat()]["envio"] = 0.0
        d += timedelta(days=1)
    avisos = {"sin_pagonube": 0, "productos_sin_costo": 0, "envio_sin_dato": 0, "envio_sin_zona": 0}
    for o in ordenes:
        if o.get("status") == "cancelled" or o.get("payment_status") != "paid":
            continue
        dia = _dia_ar(o["created_at"]).isoformat()
        if dia not in dias:
            continue
        t = dias[dia]
        total = _num(o.get("total"))
        t["orders"] += 1
        t["revenue"] += total
        for p in o.get("products") or []:
            c = _num(p.get("cost"))
            if c <= 0:
                avisos["productos_sin_costo"] += 1
            t["costo"] += c * _num(p.get("quantity"))
        neto = netos.get(str(o.get("number")))
        if neto is None:
            if o.get("gateway") == "pago-nube":
                avisos["sin_pagonube"] += 1
            neto = total
        t["neta"] += neto
        if zonas is not None:
            c, motivo = costo_envio(o, zonas)
            t["envio"] += c
            if motivo:
                avisos["envio_" + motivo] += 1
    return dias, avisos


def documento(clave, t):
    """Campos del día con las mismas claves que usa la rutina del Scorecard."""
    doc = {
        clave: {"orders": t["orders"], "revenue": round(t["revenue"], 2)},
        f"facturacion_neta_{clave}": round(t["neta"], 2),
        f"costo_productos_{clave}": round(t["costo"], 2),
    }
    if "envio" in t:
        doc[f"costo_envio_{clave}"] = round(t["envio"], 2)
    return doc


def guardar_dia(config, fecha, campos):
    sb = db._sb(config)
    r = requests.post(f"{sb['url']}/rest/v1/rpc/scorecard_guardar_dia", headers=db._headers(config),
                      json={"p_fecha": fecha, "p_campos": campos}, timeout=30)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"scorecard_guardar_dia({fecha}) falló: {r.status_code} {r.text[:300]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", help="YYYY-MM-DD (por defecto, los últimos --dias días hasta ayer)")
    ap.add_argument("--dias", type=int, default=7)
    ap.add_argument("--aplicar", action="store_true", help="guardar en Supabase (sin esto solo muestra)")
    ap.add_argument("--solo", choices=list(TIENDAS), help="procesar una sola tienda (ej. tn_lavan)")
    args = ap.parse_args()
    config = db.load_config()
    hasta = datetime.now(AR).date() - timedelta(days=1)       # el día de hoy está a medias: se carga mañana
    desde = date.fromisoformat(args.desde) if args.desde else hasta - timedelta(days=args.dias - 1)
    errores = []
    for key, (tienda, clave) in TIENDAS.items():
        cfg = config["channels"].get(key, {})
        if args.solo and key != args.solo:
            continue
        if not cfg.get("enabled", True):
            continue
        try:
            ordenes = traer_ordenes(cfg, desde, hasta)
            zonas = None
            if key in ("tn_pret", "tn_lavan"):                   # Laván paga lo mismo que Pret según la zona
                pret = config["channels"].get("tn_pret", {})
                zonas = mapa_zonas(traer_ordenes(pret, hasta - timedelta(days=DIAS_ZONAS), hasta)) if pret else {}
            dias, avisos = calcular(ordenes, netos_pagonube(config, tienda, desde), desde, hasta, zonas)
            for fecha, t in sorted(dias.items()):
                core.tnlog(f"{key} {fecha}: {t['orders']} órdenes · ventas {t['revenue']:,.0f} · neta {t['neta']:,.0f} · costo {t['costo']:,.0f}"
                           + (f" · envío {t['envio']:,.0f}" if "envio" in t else ""))
                if args.aplicar:
                    guardar_dia(config, fecha, documento(clave, t))
            core.tnlog(f"  ✓ {cfg.get('label', key)}: {len(dias)} días {'guardados' if args.aplicar else '(simulación, no se guardó)'}"
                       + (f" · ⚠ {avisos['sin_pagonube']} órdenes de Pago Nube sin neto (se usó el total)" if avisos["sin_pagonube"] else "")
                       + (f" · ⚠ {avisos['productos_sin_costo']} productos sin costo cargado" if avisos["productos_sin_costo"] else "")
                       + (f" · ⚠ envío sin sumar en {avisos['envio_sin_dato']} órdenes (Correo/Andreani: el monto no viene en la API)" if avisos["envio_sin_dato"] else "")
                       + (f" · ⚠ {avisos['envio_sin_zona']} órdenes con código postal sin zona (envío en 0)" if avisos["envio_sin_zona"] else ""))
        except Exception as e:
            errores.append(f"{key}: {e}")
            core.tnlog(f"  ⚠ {key}: {e}")
    if errores:
        raise SystemExit("Terminó con errores:\n" + "\n".join(errores))


if __name__ == "__main__":
    main()
