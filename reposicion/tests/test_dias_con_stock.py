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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    core.set_stock_days({})
