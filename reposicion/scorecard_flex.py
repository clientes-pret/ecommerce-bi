#!/usr/bin/env python3
"""
reposicion/scorecard_flex.py — Costo estimado de los envíos Flex de Mercado Libre (lo usa scorecard_ml_sync).

Mercado Libre no descuenta de la venta lo que se le paga a la mensajería (Zeta / Ninja) por Flex. Acá se estima para
poder compararlo contra lo que se paga: es informativo, NO se resta de la rentabilidad.

- Envío Flex = envío con logistic_type 'self_service', no cancelado. Se cuenta una vez por envío (un carrito con varias
  órdenes es un solo envío), en el día de venta (cierre) de su primera orden, no en el día de entrega.
- Zona = código postal del comprador, con el tramo que Tiendanube de Pret le asigna a ese código en las ventas con
  "Envío en el día" (CABA, GBA 1, 2 y 3) de los últimos 150 días. Un código de Capital que no esté en la tabla va como
  CABA; cualquier otro sin tabla se cuenta como envío "sin zona" y no suma costo.
- Tarifa = la misma que se paga en Tiendanube (TARIFA_PRET).

Se guarda en la tabla scorecard_flex_dia. Pruebas: python3 -m reposicion.tests.test_scorecard_ml
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from reposicion import core, db
from reposicion.scorecard_tn_sync import TARIFA_PRET, mapa_zonas, traer_ordenes

DIAS_ZONAS_FLEX = 150
TABLA = "scorecard_flex_dia"


def zonas_flex(config, hasta):
    """{código postal: tarifa} a partir de las ventas de Tiendanube de Pret."""
    pret = config["channels"]["tn_pret"]
    return mapa_zonas(traer_ordenes(pret, hasta - timedelta(days=DIAS_ZONAS_FLEX), hasta))


def _envio(token, sid):
    for intento in range(3):
        j = core.ml_get(f"https://api.mercadolibre.com/shipments/{sid}", token)
        if j:
            return sid, j
        time.sleep(2 * (intento + 1))
    return sid, None


def traer_envios(token, ids):
    """{id de envío: datos del envío}; None = no se pudo leer."""
    with ThreadPoolExecutor(max_workers=6) as ex:
        return dict(ex.map(lambda sid: _envio(token, sid), ids))


def envios_por_dia(ordenes, cuenta, dia_cierre, desde, hasta):
    """{id de envío: día} de las órdenes que cuentan, con el día de la primera orden de cada envío, dentro del rango."""
    primero = {}
    for o in ordenes:
        sid = (o.get("shipping") or {}).get("id")
        if not sid or not cuenta(o):
            continue
        dia = dia_cierre(o)
        if sid not in primero or dia < primero[sid]:
            primero[sid] = dia
    return {sid: d for sid, d in primero.items() if desde <= d <= hasta}


def tarifa(cp, zonas):
    """Tarifa del código postal, o None si no se puede ubicar."""
    cp = "".join(ch for ch in str(cp or "") if ch.isdigit())[:4]
    if cp in zonas:
        return zonas[cp]
    if cp and 1000 <= int(cp) <= 1499:                  # Capital Federal
        return TARIFA_PRET["Envío en el día - CABA"]
    return None


def calcular_flex(dias_envio, envios, zonas, desde, hasta):
    """{fecha ISO: {envios, costo, sin_zona, completo}}. Un día con algún envío sin leer queda incompleto."""
    dias = {}
    d = desde
    while d <= hasta:
        dias[d.isoformat()] = {"envios": 0, "costo": 0.0, "sin_zona": 0, "completo": True}
        d += timedelta(days=1)
    for sid, dia in dias_envio.items():
        t = dias[dia.isoformat()]
        e = envios.get(sid)
        if e is None:
            t["completo"] = False
            continue
        if e.get("logistic_type") != "self_service" or e.get("status") == "cancelled":
            continue
        t["envios"] += 1
        costo = tarifa((e.get("receiver_address") or {}).get("zip_code"), zonas)
        if costo is None:
            t["sin_zona"] += 1
        else:
            t["costo"] += costo
    return dias


def guardar(config, marca, dias):
    """Guarda los días completos (los incompletos conservan lo que ya había)."""
    ahora = datetime.now(timezone.utc).isoformat()
    filas = [{"fecha": f, "marca": marca, "envios": t["envios"], "costo": round(t["costo"], 2),
              "sin_zona": t["sin_zona"], "actualizado_en": ahora} for f, t in sorted(dias.items()) if t["completo"]]
    db.upsert(config, TABLA, filas, "fecha,marca")
