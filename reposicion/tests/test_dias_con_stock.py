"""Velocidad = ventas / días CON STOCK, no / días de vida del producto.

Correr:  python3 -m reposicion.tests.test_dias_con_stock
"""
from reposicion import core


def _filas(sku, n, dep=5, fp=0, fl=0):
    return [{"fecha": f"2026-10-{i:02d}", "sku": sku, "stock_deposito": dep,
             "stock_full_pret": fp, "stock_full_lavan": fl} for i in range(1, n + 1)]


def test_reingreso_hace_10_dias_vende_1_por_dia():
    # Caso del usuario: producto viejo (60 días de ventana), stock hace 10 días, 10 ventas.
    core.set_stock_days(core.contar_dias_con_stock(_filas("A", 10)))
    dias = core.dias_efectivos("A", "deposito", 60)
    assert dias == 10
    assert round(10 / dias, 4) == 1.0


def test_nunca_supera_la_antiguedad_del_producto():
    core.set_stock_days(core.contar_dias_con_stock(_filas("A", 30)))
    assert core.dias_efectivos("A", "deposito", 20) == 20  # producto creado hace 20 días


def test_sin_historial_o_sin_dias_con_stock_usa_la_base():
    core.set_stock_days(core.contar_dias_con_stock(_filas("A", 10)))
    assert core.dias_efectivos("OTRO", "deposito", 60) == 60       # SKU sin snapshots
    assert core.dias_efectivos("A", "full_pret", 60) == 60         # nunca tuvo stock en ese pool
    core.set_stock_days({})
    assert core.dias_efectivos("A", "deposito", 60) == 60          # sin historial cargado


def test_pools_separados_y_full_combinado():
    # Una fila por (fecha, sku), como en la tabla: Pret tiene stock 10 días, Lavan los primeros 4.
    f = [{"fecha": f"2026-10-{i:02d}", "sku": "A", "stock_deposito": 0,
          "stock_full_pret": 3, "stock_full_lavan": 2 if i <= 4 else 0} for i in range(1, 11)]
    d = core.contar_dias_con_stock(f)["A"]
    assert (d["deposito"], d["full_pret"], d["full_lavan"]) == (0, 10, 4)
    assert d["full"] == 10  # días con stock en cualquiera de los dos pools (se solapan)


def test_base_cero_se_respeta():
    core.set_stock_days(core.contar_dias_con_stock(_filas("A", 10)))
    assert core.dias_efectivos("A", "deposito", 0) == 0


def _con_now(now_iso):
    from datetime import datetime
    core.configure(days=60, coverage_days=60, now=datetime.fromisoformat(now_iso))


def test_dias_con_stock_de_los_ultimos_7():
    _con_now("2026-10-31T12:00:00+00:00")  # corte = 2026-10-24: cuentan snapshots > 24 (25..31)
    f = _filas("A", 31)  # stock todos los días de octubre
    d = core.contar_dias_con_stock(f)["A"]
    assert d["deposito"] == 31
    assert d["deposito_7d"] == 7
    core.configure(days=60, coverage_days=60)


def test_pico_reciente_gana_al_promedio():
    # Producto con stock 60 días, 60 ventas en 60 días (1/día) pero 42 en los últimos 7 (6/día).
    _con_now("2026-10-31T12:00:00+00:00")
    filas = [{"fecha": f"2026-{m}-{d:02d}", "sku": "A", "stock_deposito": 9, "stock_full_pret": 0, "stock_full_lavan": 0}
             for m, rng in (("09", range(2, 31)), ("10", range(1, 32))) for d in rng]
    core.set_stock_days(core.contar_dias_con_stock(filas))
    promedio = 60 / core.dias_efectivos("A", "deposito", 60)
    reciente = core.vel_reciente(42, "A", "deposito", 60)
    assert round(promedio, 2) == 1.0 and round(reciente, 2) == 6.0
    # Un pico reciente sube la velocidad enseguida, pero solo con el peso RECENT_WEIGHT (0,25)
    # del exceso: 1 + 0,25 × (6 − 1) = 2,25 (antes max() = 6, sobreestimaba 32 % en backtest).
    assert core.combinar_velocidad(promedio, reciente) == 1.0 + core.RECENT_WEIGHT * 5.0
    assert round(core.combinar_velocidad(promedio, reciente), 2) == 2.25
    core.set_stock_days({})
    core.configure(days=60, coverage_days=60)


def test_baja_reciente_no_baja_la_velocidad():
    # Criterio del usuario (2026-11-05): una baja reciente no baja la velocidad de golpe.
    assert core.combinar_velocidad(5.0, 1.0) == 5.0
    assert core.combinar_velocidad(3.0, 0) == 3.0   # sin ventas recientes manda el promedio
    assert core.combinar_velocidad(0, 0) == 0


def test_ritmo_reciente_no_castiga_dias_sin_stock():
    # Estuvo sin stock 4 de los últimos 7 días (3 con stock): 9 ventas → 3/día, no 9/7.
    _con_now("2026-10-31T12:00:00+00:00")
    filas = [{"fecha": f"2026-10-{d}", "sku": "A", "stock_deposito": 5, "stock_full_pret": 0, "stock_full_lavan": 0}
             for d in (29, 30, 31)]
    core.set_stock_days(core.contar_dias_con_stock(filas))
    assert core.vel_reciente(9, "A", "deposito", 60) == 3.0
    core.set_stock_days({})
    core.configure(days=60, coverage_days=60)


def test_sin_ventas_recientes_manda_el_promedio_y_tope_por_antiguedad():
    core.set_stock_days({})
    assert core.vel_reciente(0, "A", "deposito", 60) == 0
    assert core.vel_reciente(6, "A", "deposito", 3) == 2.0  # producto de 3 días: divisor 3, no 7


def test_muestra_minima_poca_data_no_se_extrapola():
    # Caso 243090VIDRIO: 3 ventas, 4 días con stock → divisor 14 (no 4): 0,21/día, no 0,75.
    core.set_stock_days(core.contar_dias_con_stock(_filas("A", 4)))
    assert core.dias_efectivos("A", "deposito", 60) == 4
    assert core.dias_para_promedio("A", "deposito", 60, 3) == 14
    assert round(3 / core.dias_para_promedio("A", "deposito", 60, 3), 2) == 0.21
    core.set_stock_days({})


def test_muestra_minima_no_toca_ventas_altas_ni_datos_suficientes():
    core.set_stock_days(core.contar_dias_con_stock(_filas("A", 4)))
    assert core.dias_para_promedio("A", "deposito", 60, 97) == 4    # lanzamiento fuerte: evidencia real
    assert core.dias_para_promedio("A", "deposito", 60, 10) == 4    # justo en el umbral: no se toca
    assert core.dias_para_promedio("A", "deposito", 60, 9) == 14
    assert core.dias_para_promedio("A", "deposito", 60, 0) == 4     # sin ventas: sin efecto (vel = 0)
    core.set_stock_days(core.contar_dias_con_stock(_filas("B", 20)))
    assert core.dias_para_promedio("B", "deposito", 60, 3) == 20    # ya tiene >= 14 días con stock
    core.set_stock_days({})


def test_muestra_minima_acotada_por_antiguedad_del_producto():
    core.set_stock_days(core.contar_dias_con_stock(_filas("A", 4)))
    assert core.dias_para_promedio("A", "deposito", 6, 3) == 6      # producto de 6 días: tope 6, no 14
    assert core.dias_para_promedio("A", "deposito", 0, 3) == 0
    core.set_stock_days({})


def _orden(fecha, sku, qty):
    return {"date_created": f"{fecha}T15:00:00+00:00",
            "order_items": [{"item": {"id": "MLA1", "seller_sku": sku}, "quantity": qty}]}


_PUB_FULL = {"MLA1": {"shipping": {"logistic_type": "fulfillment"}, "seller_custom_field": "A"}}


def test_ventas_desde_deposito_de_una_publicacion_que_paso_a_full():
    # Caso PH-FUN-A-LIN1VERDECLARO: 15 días con depósito, entra a Full el último día,
    # 20 ventas anteriores que hoy se clasifican Full. No son 20/día: son 20/15.
    filas = [{"fecha": f"2026-11-{i:02d}", "sku": "A", "stock_deposito": 60,
              "stock_full_pret": 20 if i == 26 else 0, "stock_full_lavan": 0} for i in range(12, 27)]
    _con_now("2026-11-26T19:00:00+00:00")
    core.set_stock_days(core.contar_dias_con_stock(filas))
    ordenes = [_orden("2026-11-16", "A", 7), _orden("2026-11-18", "A", 9), _orden("2026-11-21", "A", 4)]
    assert core.skus_full_sin_stock(ordenes, _PUB_FULL, "full_pret") == {"A"}
    assert core.dias_efectivos("A", "full_pret", 15) == 1
    assert core.dias_efectivos("A", "vendible_pret", 15) == 15
    core.set_stock_days({})
    core.configure(days=60, coverage_days=60)


def test_ventas_con_stock_en_full_no_se_marcan():
    # Stock en Full del 1 al 10; una venta el 11 (foto del 10 con stock) tampoco marca.
    core.set_stock_days(core.contar_dias_con_stock(_filas("A", 10, dep=5, fp=3)))
    ordenes = [_orden("2026-10-03", "A", 2), _orden("2026-10-11", "A", 1)]
    assert core.skus_full_sin_stock(ordenes, _PUB_FULL, "full_pret") == set()
    core.set_stock_days({})


def test_sin_historial_no_se_marca_nada():
    core.set_stock_days({})
    assert core.skus_full_sin_stock([_orden("2026-10-03", "A", 2)], _PUB_FULL, "full_pret") == set()


def test_build_rows_publicacion_recien_pasada_a_full():
    # De punta a punta, con las fechas y cantidades reales de PH-FUN-A-LIN1VERDECLARO.
    _con_now("2026-11-26T19:00:00+00:00")
    core.configure(days=60, coverage_days=30, now=core.NOW)
    filas = [{"fecha": f"2026-11-{i:02d}", "sku": "A", "stock_deposito": 62,
              "stock_full_pret": 20 if i == 26 else 0, "stock_full_lavan": 0} for i in range(12, 27)]
    core.set_stock_days(core.contar_dias_con_stock(filas))
    ordenes = [_orden("2026-11-16", "A", 7), _orden("2026-11-17", "A", 4),
               _orden("2026-11-18", "A", 5), _orden("2026-11-21", "A", 4)]
    pub = {"MLA1": {"shipping": {"logistic_type": "fulfillment"}, "seller_custom_field": "A",
                    "inventory_id": "INV1", "available_quantity": 20}}
    results = {
        "tn_pret": {"products": [{"id": 1, "name": {"es": "Almohadón"}, "created_at": "2026-11-11T12:00:00+00:00",
                                  "variants": [{"id": 2, "sku": "A", "stock": 62}]}]},
        "ml_pret": {"orders": ordenes, "orders_recent": ordenes[-1:], "item_details": pub},
    }
    row = core.build_rows(results)[0]
    assert row["Unid. Full Pret"] == 20
    assert row["Vel. Full Pret (semanal)"] < 12          # antes: 140
    assert 15 <= row["A enviar Full Pret (uds)"] <= 25   # antes: 580
    assert row["Vel. semanal"] < 12
    assert row["A reponer (uds)"] == 0
    core.set_stock_days({})
    core.configure(days=60, coverage_days=60)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    core.set_stock_days({})
