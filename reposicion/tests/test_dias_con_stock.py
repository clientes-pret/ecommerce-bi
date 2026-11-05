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
    assert max(promedio, reciente) == 6.0
    core.set_stock_days({})
    core.configure(days=60, coverage_days=60)


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    core.set_stock_days({})
