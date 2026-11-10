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


def _api_ads(url, headers=None, params=None, timeout=None):
    if "product_ads" in url:
        return _resp(200, {"results": [{"date": "2026-11-07", "cost": 100.004}, {"date": "2026-11-09", "cost": 50.0}]})
    if url.endswith("/display/campaigns"):
        return _resp(200, {"results": [{"campaign_id": 1}, {"campaign_id": 2}]})
    if url.endswith("/campaigns/1/metrics"):
        return _resp(200, {"metrics": [{"date": "2026-11-07", "consumed_budget": 10.0}, {"date": "2026-11-08", "consumed_budget": 5.5}]})
    return _resp(200, {"metrics": [{"date": "2026-11-08", "consumed_budget": 4.5}]})


def test_ads_suma_product_ads_y_display_y_los_dias_sin_gasto_valen_cero():
    with mock.patch.object(scorecard_ml_sync.requests, "get", side_effect=_api_ads):
        ads = traer_ads("tok", 27450, date(2026, 11, 7), date(2026, 11, 10))
    assert ads == {"2026-11-07": 110.0, "2026-11-08": 10.0, "2026-11-09": 50.0, "2026-11-10": 0.0}


def test_ads_si_cualquier_consulta_falla_no_inventa_ni_guarda_parciales():
    def falla_display(url, **kw):
        return _resp(403, "forbidden") if "/display/" in url else _api_ads(url)
    with mock.patch.object(scorecard_ml_sync.requests, "get", side_effect=falla_display):
        try:
            traer_ads("tok", 27450, date(2026, 11, 7), date(2026, 11, 9))
        except RuntimeError as e:
            assert "403" in str(e)
        else:
            raise AssertionError("tenía que fallar")


def test_ads_cuenta_sin_display_cuenta_solo_product_ads():
    def sin_display(url, **kw):
        return _resp(401, "unauthorized") if url.endswith("/display/campaigns") else _api_ads(url)
    with mock.patch.object(scorecard_ml_sync.requests, "get", side_effect=sin_display):
        ads = traer_ads("tok", 1144462, date(2026, 11, 7), date(2026, 11, 9))
    assert ads == {"2026-11-07": 100.0, "2026-11-08": 0.0, "2026-11-09": 50.0}


if __name__ == "__main__":
    for nombre, fn in list(globals().items()):
        if nombre.startswith("test_"):
            fn()
            print("ok", nombre)
