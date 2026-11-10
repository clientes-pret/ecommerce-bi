"""Pruebas de reposicion/scorecard_ml_sync.py (lógica pura, sin red ni base de datos).

Correr:  python3 -m reposicion.tests.test_scorecard_ml
"""

from datetime import date

from unittest import mock

from reposicion import scorecard_ml_sync
from reposicion.scorecard_ml_sync import calcular, cuenta, dia_cierre, documento, traer_ads

D = date(2026, 10, 22)


def orden(oid, cierre, total, pagos=(("approved", 1),), status="paid", items=(("A", 2),), creada="2026-10-20T10:00:00.000-04:00"):
    return {"id": oid, "status": status, "total_amount": total, "date_created": creada, "date_closed": cierre,
            "payments": [{"id": 1000 * oid + i, "status": s} for i, (s, _) in enumerate(pagos)],
            "order_items": [{"item": {"seller_sku": sku}, "quantity": q} for sku, q in items]}


def test_cuenta_solo_con_pago_aprobado_y_no_cancelada():
    assert cuenta(orden(1, "2026-10-22T10:00:00.000-04:00", 100))
    assert not cuenta(orden(2, "2026-10-22T10:00:00.000-04:00", 100, pagos=(("in_mediation", 0),)))
    assert not cuenta(orden(3, "2026-10-22T10:00:00.000-04:00", 100, pagos=(("rejected", 0),)))
    assert not cuenta(orden(4, "2026-10-22T10:00:00.000-04:00", 100, status="cancelled"))
    assert cuenta(orden(5, "2026-10-22T10:00:00.000-04:00", 100, status="partially_refunded"))


def test_el_dia_es_el_de_cierre_en_hora_argentina():
    # 23:30 en -04:00 = 00:30 del día siguiente en Argentina (-03:00)
    assert dia_cierre(orden(1, "2026-10-21T23:30:00.000-04:00", 1)) == D
    assert dia_cierre(orden(1, "2026-10-22T10:59:20.000-04:00", 1)) == D
    # sin fecha de cierre usa la de creación
    assert dia_cierre(orden(1, None, 1, creada="2026-10-22T09:00:00.000-04:00")) == D


def test_totales_costo_y_neta():
    ords = [orden(1, "2026-10-22T10:00:00.000-04:00", 1000, items=(("A", 2), ("B", 1))),
            orden(2, "2026-10-22T11:00:00.000-04:00", 500, pagos=(("in_mediation", 0),)),   # no cuenta
            orden(3, "2026-10-23T11:00:00.000-04:00", 700)]                                  # otro día
    pagos = {1000: (800.0, 1000.0), 3000: (600.0, 700.0)}
    dias, avisos = calcular(ords, pagos, {"A": 100.0, "B": 50.0}, D, D)
    t = dias["2026-10-22"]
    assert (t["orders"], t["revenue"], t["costo"], t["neta"]) == (1, 1000.0, 250.0, 800.0) and t["neta_completa"]
    assert avisos["pagos_sin_dato"] == 0 and avisos["skus_sin_costo"] == {}


def test_pago_de_carrito_se_reparte_por_el_valor_de_la_orden():
    # un pago de $1000 con neto $800 cubre dos órdenes de $600 y $400
    ords = [orden(1, "2026-10-22T10:00:00.000-04:00", 600), orden(2, "2026-10-22T10:00:00.000-04:00", 400)]
    pagos = {1000: (800.0, 1000.0), 2000: (800.0, 1000.0)}
    dias, _ = calcular(ords, pagos, {"A": 1.0}, D, D)
    assert round(dias["2026-10-22"]["neta"], 2) == 800.0


def test_pago_sin_leer_deja_el_dia_sin_neta_y_sku_sin_costo_avisa():
    ords = [orden(1, "2026-10-22T10:00:00.000-04:00", 1000, items=(("ZZZ", 3),))]
    dias, avisos = calcular(ords, {1000: (None, None)}, {}, D, D)
    assert not dias["2026-10-22"]["neta_completa"] and avisos["pagos_sin_dato"] == 1
    assert avisos["skus_sin_costo"] == {"ZZZ": 3}
    assert "facturacion_neta_ml_pret_home" not in documento("ml_pret_home", dias["2026-10-22"])


def test_documento_completo():
    dias, _ = calcular([], {}, {}, D, D)
    assert documento("ml_casa_lavan", dias["2026-10-22"]) == {
        "ml_casa_lavan": {"orders": 0, "revenue": 0.0}, "costo_productos_ml_casa_lavan": 0.0,
        "facturacion_neta_ml_casa_lavan": 0.0}


def _resp(codigo, cuerpo=None):
    r = mock.Mock(status_code=codigo, text=str(cuerpo))
    r.json.return_value = cuerpo
    return r


def test_ads_dias_sin_gasto_valen_cero_y_se_devuelven_todos_los_dias():
    cuerpo = {"results": [{"date": "2026-11-07", "cost": 656429.774}, {"date": "2026-11-09", "cost": 921888.95}]}
    with mock.patch.object(scorecard_ml_sync.requests, "get", return_value=_resp(200, cuerpo)):
        ads = traer_ads("tok", 27450, date(2026, 11, 7), date(2026, 11, 9))
    assert ads == {"2026-11-07": 656429.77, "2026-11-08": 0.0, "2026-11-09": 921888.95}


def test_ads_si_la_api_falla_no_inventa_ceros():
    with mock.patch.object(scorecard_ml_sync.requests, "get", return_value=_resp(403, "forbidden")):
        try:
            traer_ads("tok", 27450, date(2026, 11, 7), date(2026, 11, 9))
        except RuntimeError as e:
            assert "403" in str(e)
        else:
            raise AssertionError("tenía que fallar")


if __name__ == "__main__":
    for nombre, fn in list(globals().items()):
        if nombre.startswith("test_"):
            fn()
            print("ok", nombre)
