"""Pruebas de reposicion/scorecard_tn_sync.py (lógica pura, sin red ni base de datos).

Correr:  python3 -m reposicion.tests.test_scorecard_tn
"""

from datetime import date

from reposicion.scorecard_tn_sync import calcular, costo_envio, documento, mapa_zonas


def orden(numero, creada, total, estado="paid", gateway="pago-nube", productos=(("A", 2, 100),), status="open"):
    return {"number": numero, "created_at": creada, "total": str(total), "payment_status": estado, "status": status,
            "gateway": gateway, "products": [{"sku": s, "quantity": q, "cost": str(c)} for s, q, c in productos]}


D = date(2026, 10, 19)


def test_solo_pagadas_no_canceladas():
    ords = [orden(1, "2026-10-19T15:00:00+0000", 1000),
            orden(2, "2026-10-19T15:00:00+0000", 500, estado="partially_refunded"),
            orden(3, "2026-10-19T15:00:00+0000", 700, estado="voided", status="cancelled"),
            orden(4, "2026-10-19T15:00:00+0000", 300, estado="refunded")]
    dias, _ = calcular(ords, {}, D, D)
    assert dias["2026-10-19"]["orders"] == 1 and dias["2026-10-19"]["revenue"] == 1000


def test_dia_en_hora_argentina():
    # 02:00 UTC del 20 = 23:00 del 19 en Argentina
    ords = [orden(1, "2026-10-20T02:00:00+0000", 800)]
    dias, _ = calcular(ords, {}, D, date(2026, 10, 20))
    assert dias["2026-10-19"]["orders"] == 1 and dias["2026-10-20"]["orders"] == 0


def test_neta_usa_pagonube_y_total_si_no_hay():
    ords = [orden(1, "2026-10-19T15:00:00+0000", 1000), orden(2, "2026-10-19T16:00:00+0000", 400, gateway="gocuotas")]
    dias, avisos = calcular(ords, {"1": 900.0}, D, D)
    assert dias["2026-10-19"]["neta"] == 1300 and avisos["sin_pagonube"] == 0
    dias, avisos = calcular(ords[:1], {}, D, D)           # orden de Pago Nube sin neto: usa el total y avisa
    assert dias["2026-10-19"]["neta"] == 1000 and avisos["sin_pagonube"] == 1


def test_costo_por_cantidad_y_aviso_sin_costo():
    ords = [orden(1, "2026-10-19T15:00:00+0000", 1000, productos=(("A", 2, 100), ("B", 1, 50), ("C", 1, 0)))]
    dias, avisos = calcular(ords, {}, D, D)
    assert dias["2026-10-19"]["costo"] == 250 and avisos["productos_sin_costo"] == 1


def test_dia_sin_ventas_queda_en_cero_y_documento():
    dias, _ = calcular([], {}, D, D)
    assert documento("tn_casa_lavan", dias["2026-10-19"]) == {
        "tn_casa_lavan": {"orders": 0, "revenue": 0.0}, "facturacion_neta_tn_casa_lavan": 0.0,
        "costo_productos_tn_casa_lavan": 0.0}


def con_envio(numero, opcion, cp, creada="2026-10-19T15:00:00+0000", estado="paid"):
    o = orden(numero, creada, 1000, estado=estado)
    o.update({"shipping_option": opcion, "shipping_address": {"zipcode": cp}})
    return o


def test_mapa_zonas_toma_el_tramo_mas_frecuente_de_pret():
    pret = [con_envio(1, "Envío en el día - GBA 1", "1714"), con_envio(2, "Envío en el día - GBA 1", "1714"),
            con_envio(3, "Envío en el día - GBA 2", "1714"), con_envio(4, "Envío en el día - GBA 2", "1670"),
            con_envio(5, "Envío en el día - Gratis", "1888"), con_envio(6, "Punto de retiro", "1900"),
            con_envio(7, "Envío en el día - GBA 3", "1900", estado="refunded")]
    z = mapa_zonas(pret)
    assert z == {"1714": 4500.0, "1670": 6000.0}          # Gratis, retiro y no pagadas no cuentan


def test_costo_envio_laván():
    z = {"1714": 4500.0}
    assert costo_envio(con_envio(1, "Ninja Flex - Flex", "1714-"), z) == (4500.0, None)
    assert costo_envio(con_envio(2, "Envío Express 24/48 hs GBA", "1999"), z) == (0.0, "sin_zona")
    assert costo_envio(con_envio(3, "Envio Express 24/48 hs. CABA", "1048"), z) == (2800.0, None)   # CABA por rango
    assert costo_envio(con_envio(4, "Punto de retiro", ""), z) == (0.0, None)
    assert costo_envio(con_envio(5, "Envío Nube - Correo Argentino Clásico a domicilio", "1714"), z) == (0.0, "sin_dato")


def test_calcular_suma_envio_solo_con_zonas_y_documento():
    ords = [con_envio(1, "Ninja Flex - Flex", "1714"), con_envio(2, "Envío Nube - Correo Argentino Clásico a domicilio", "1714"),
            con_envio(3, "Punto de retiro", "")]
    dias, avisos = calcular(ords, {}, D, D, {"1714": 4500.0})
    assert dias["2026-10-19"]["envio"] == 4500 and avisos["envio_sin_dato"] == 1
    assert documento("tn_casa_lavan", dias["2026-10-19"])["costo_envio_tn_casa_lavan"] == 4500.0
    dias, _ = calcular(ords, {}, D, D)                      # sin zonas (Pret): no hay costo_envio, la rutina lo sigue cargando
    assert "envio" not in dias["2026-10-19"] and "costo_envio_tn_pret_home" not in documento("tn_pret_home", dias["2026-10-19"])


if __name__ == "__main__":
    for nombre, fn in list(globals().items()):
        if nombre.startswith("test_"):
            fn()
            print("ok", nombre)
