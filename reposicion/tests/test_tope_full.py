"""Tope de la sugerencia de Full: ritmo de venta de calendario (pedido del usuario 2026-11-27).

Correr:  python3 -m reposicion.tests.test_tope_full
"""
from datetime import date, datetime

from reposicion import core
from reposicion.jobs.weekly_calc import resumir_ventas_largas, row_to_calculo


def _preparar():
    core.configure(days=60, coverage_days=30, now=datetime.fromisoformat("2026-11-27T12:00:00+00:00"))
    core.set_stock_days({})


def test_agotado_largo_con_los_numeros_reales():
    # PRET-BO150X140BLANCO: 493 ventas Full en 60 d y 894 en ML en 90 d, sin stock. Panel: 259; antes pedíamos 643.
    _preparar()
    assert core.tope_full(0, 493, 894, 400) == 298
    # COR-TROPICAL130X205NATURAL: manda la ventana Full (373/60 > 542/90). Panel: 134; antes 236.
    assert core.tope_full(29, 373, 542, 400) == 158


def test_producto_nuevo_se_mide_sobre_los_dias_que_existe():
    _preparar()
    assert core.tope_full(0, 40, 40, 10) == 120    # 4 por día
    assert core.tope_full(0, 40, 40, 400) == 20    # el mismo total en un producto viejo


def test_sin_ventas_o_con_stock_de_sobra():
    _preparar()
    assert core.tope_full(0, 0, 0, 400) == 0
    assert core.tope_full(500, 60, 90, 400) == 0


def test_edad_en_dias_no_se_recorta_a_la_ventana():
    _preparar()
    assert core.edad_en_dias("2026-11-17T12:00:00+00:00") == 10
    assert core.edad_en_dias("2025-11-27T12:00:00+00:00") == 365
    assert core.edad_en_dias("") == core.DIAS_TOPE_FULL


def test_resumir_ventas_usa_el_sku_de_la_publicacion_si_falta():
    filas = [{"sku": "A", "item_id": "MLA1", "cantidad": 2, "fecha": "2026-10-01"},
             {"sku": "", "item_id": "MLA2", "cantidad": 3, "fecha": "2026-09-01"},
             {"sku": "", "item_id": "MLA9", "cantidad": 7, "fecha": "2026-09-01"}]
    detalle = {"MLA2": {"seller_custom_field": "A"}}
    assert resumir_ventas_largas(filas, detalle, date(2026, 11, 27)) == {"A": (5, 87)}


def _resultados(creado, ventas_largas_pret=None, ventas_largas_lavan=None):
    # Dos cuentas con el mismo SKU: 60 ventas Full cada una en los primeros días, ambas agotadas hoy.
    def orden(item, dia):
        return {"date_created": f"2026-10-{dia:02d}T15:00:00+00:00",
                "order_items": [{"item": {"id": item, "seller_sku": "A"}, "quantity": 10}]}

    def cuenta(item, inv, largas):
        pub = {item: {"shipping": {"logistic_type": "fulfillment"}, "seller_custom_field": "A",
                      "inventory_id": inv, "available_quantity": 0}}
        r = {"orders": [orden(item, d) for d in range(1, 7)], "orders_recent": [], "item_details": pub}
        if largas is not None:
            r["ventas_largas"] = largas
        return r

    return {"tn_pret": {"products": [{"id": 1, "name": {"es": "Cortina"}, "created_at": creado,
                                      "variants": [{"id": 2, "sku": "A", "stock": 0}]}]},
            "ml_pret": cuenta("MLA1", "I1", ventas_largas_pret),
            "ml_lavan": cuenta("MLA2", "I2", ventas_largas_lavan)}


def _stock_seis_dias():
    return [{"fecha": f"2026-10-{d:02d}", "sku": "A", "stock_deposito": 0, "stock_full_pret": 5, "stock_full_lavan": 5}
            for d in range(1, 7)]


def test_build_rows_recorta_por_marca_y_guarda_el_numero_sin_tope():
    _preparar()
    core.set_stock_days(core.contar_dias_con_stock(_stock_seis_dias()))
    row = core.build_rows(_resultados("2025-01-01T00:00:00+00:00", {"A": (90, 57)}, {"A": (900, 57)}))[0]
    # 60 ventas en 6 días con stock → 10/día → 300 sin tope.
    assert row["A enviar Full Pret sin tope (uds)"] == 300
    assert row["A enviar Full Pret (uds)"] == 30           # 60/60 = 90/90 = 1 por día
    assert row["A enviar Full Lavan (uds)"] == 300         # 900/90 = 10 por día: el tope no sube ni recorta
    calc = row_to_calculo(row, "2026-W48", 30)
    assert (calc["a_reponer_full_pret"], calc["a_reponer_full_pret_sin_tope"]) == (30, 300)
    core.set_stock_days({})


def test_el_tope_nunca_sube_la_sugerencia():
    _preparar()
    core.set_stock_days(core.contar_dias_con_stock(_stock_seis_dias()))
    row = core.build_rows(_resultados("2025-01-01T00:00:00+00:00", {"A": (9000, 80)}, {}))[0]
    assert row["A enviar Full Pret (uds)"] == row["A enviar Full Pret sin tope (uds)"]
    assert row["A enviar Full Lavan (uds)"] == 30          # sin ventas largas manda la ventana Full: 60/60
    core.set_stock_days({})


def test_producto_recreado_en_tiendanube_no_cuenta_como_nuevo():
    _preparar()
    core.set_stock_days(core.contar_dias_con_stock(_stock_seis_dias()))
    # Creado hace 3 días en Tiendanube, pero con ventas desde hace 80: se mide sobre 80, no sobre 3.
    row = core.build_rows(_resultados("2026-11-24T00:00:00+00:00", {"A": (80, 80)}, None))[0]
    assert row["A enviar Full Pret (uds)"] <= 30
    core.set_stock_days({})


def test_sin_datos_largos_no_hay_tope():
    _preparar()
    core.set_stock_days(core.contar_dias_con_stock(_stock_seis_dias()))
    row = core.build_rows(_resultados("2025-01-01T00:00:00+00:00"))[0]
    assert row["A enviar Full Pret (uds)"] == row["A enviar Full Pret sin tope (uds)"] == 300
    core.set_stock_days({})


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    core.set_stock_days({})
