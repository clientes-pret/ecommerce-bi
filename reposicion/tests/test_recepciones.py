"""Pruebas de reposicion/recepciones.py (lógica pura, sin base de datos).

Correr:  python3 -m reposicion.tests.test_recepciones
"""

from datetime import datetime, timezone

from reposicion.recepciones import calcular_recepciones, estimaciones_a_guardar, estimar_ingreso

UTC = timezone.utc


def dt(dia, hora=12):
    return datetime(2026, 10, dia, hora, tzinfo=UTC)


def snap(dia, total, hora=12):
    return {"fecha": f"2026-10-{dia:02d}", "captured_at": dt(dia, hora), "total": total}


def linea(i, pedido, destino, pedida, recibida=0, completo=False):
    return {"id": i, "pedido_id": pedido, "sku": "X", "destino": destino,
            "cantidad_pedida": pedida, "cantidad_recibida": recibida, "recibido_completo": completo}


def correr(items, pedidos, snaps, ventas, hoy):
    return {c["item_id"]: c for c in calcular_recepciones({"X": items}, pedidos, {"X": snaps}, {"X": ventas}, {"X": hoy})}


def test_vendedor_rapido_las_ventas_no_tapan_el_ingreso():
    # Caso real (pedido #15): stock 20 al pedir, 3 hoy, 325 vendidas → entraron ~308 de 236 pedidas.
    r = correr([linea(1, 1, "full_pret", 236)], {1: {"creado_at": dt(1), "unidad_medida": "unidades"}},
               [snap(1, 20), snap(16, 3)], [("2026-10-05", 325)], 3)
    assert r[1]["completo"] and r[1]["ahora"] == 236


def test_rotacion_lenta_sin_ventas():
    r = correr([linea(1, 1, "deposito", 50)], {1: {"creado_at": dt(1), "unidad_medida": "unidades"}},
               [snap(1, 10)], [], 60)
    assert r[1]["completo"]


def test_pedido_para_full_que_entra_por_deposito():
    # Llegan 60 de 100 pedidas (50 depósito + 50 Full): el pool junto sube 60,
    # se llena primero la línea de depósito y el resto va a Full.
    items = [linea(1, 1, "deposito", 50), linea(2, 1, "full_pret", 50)]
    r = correr(items, {1: {"creado_at": dt(1), "unidad_medida": "unidades"}}, [snap(1, 100)], [], 160)
    assert r[1]["completo"] and r[2]["ahora"] == 10 and not r[2]["completo"]


def test_lo_recibido_nunca_baja():
    r = correr([linea(1, 1, "deposito", 100, recibida=30)], {1: {"creado_at": dt(1), "unidad_medida": "unidades"}},
               [snap(1, 50)], [], 60)  # ingreso actual 10 < 30 ya registrado
    assert 1 not in r


def test_linea_cargada_a_mano_tambien_recibe_sugerencia():
    # El usuario cargó 5 de 100 y el stock sugiere que entraron 60: se sugiere 60 (no 65).
    manual = linea(1, 1, "deposito", 100, recibida=5)
    manual["recibido_manual"] = True
    r = correr([manual], {1: {"creado_at": dt(1), "unidad_medida": "unidades"}}, [snap(1, 50)], [], 110)
    assert r[1]["antes"] == 5 and r[1]["ahora"] == 60


def test_reemplazadas_y_pasadas_no_participan():
    # La vieja fue reemplazada: el ingreso se sugiere en la vigente.
    vieja = linea(1, 1, "deposito", 100, completo=True)
    vieja["reemplazado_por"] = 2
    pasada = linea(3, 1, "deposito", 40, completo=True)
    pasada["movido_a"] = 9
    pedidos = {1: {"creado_at": dt(1), "unidad_medida": "unidades"}, 2: {"creado_at": dt(3), "unidad_medida": "unidades"}}
    r = correr([vieja, pasada, linea(2, 2, "deposito", 150)], pedidos, [snap(1, 0), snap(3, 0)], [], 60)
    assert list(r) == [2] and r[2]["ahora"] == 60


def test_cerrada_con_faltante_solo_consume_lo_que_llego():
    cerrada = linea(1, 1, "deposito", 100, recibida=10, completo=True)
    cerrada["cerrado_con_faltante"] = True
    pedidos = {1: {"creado_at": dt(1), "unidad_medida": "unidades"}, 2: {"creado_at": dt(1, 13), "unidad_medida": "unidades"}}
    abierta = linea(2, 2, "deposito", 50)
    r = correr([cerrada, abierta, linea(3, 1, "full_pret", 1)], pedidos, [snap(1, 0)], [], 70)
    assert r[2]["ahora"] == 50


def test_solo_se_guardan_sugerencias_que_cambian():
    a = linea(1, 1, "deposito", 100); a["estimado_recibido"] = 60      # igual que antes: no se reescribe
    b = linea(2, 1, "deposito", 100); b["estimado_recibido"] = 30      # ya no hay sugerencia: se borra
    c = linea(3, 1, "deposito", 100)                                    # nueva
    d = linea(4, 1, "deposito", 100, completo=True)                     # cerrada: no se toca
    cambios = [{"item_id": 1, "antes": 0, "ahora": 60}, {"item_id": 3, "antes": 0, "ahora": 8}]
    assert estimaciones_a_guardar({"X": [a, b, c, d]}, cambios) == {2: None, 3: 8}


def test_un_mismo_ingreso_no_cierra_dos_pedidos():
    # Dos pedidos de 100 del mismo SKU; entraron 100 en total → solo el más viejo cierra.
    items = [linea(1, 1, "deposito", 100), linea(2, 2, "deposito", 100)]
    pedidos = {1: {"creado_at": dt(1), "unidad_medida": "unidades"}, 2: {"creado_at": dt(3), "unidad_medida": "unidades"}}
    r = correr(items, pedidos, [snap(1, 0), snap(3, 0)], [], 100)
    assert r[1]["completo"] and 2 not in r


def test_pedidos_en_bultos_no_se_tocan():
    r = correr([linea(1, 1, "deposito", 12)], {1: {"creado_at": dt(1), "unidad_medida": "bultos"}},
               [snap(1, 0)], [], 500)
    assert r == {}


def test_sin_foto_previa_al_pedido_no_hay_evidencia():
    r = correr([linea(1, 1, "deposito", 10)], {1: {"creado_at": dt(1), "unidad_medida": "unidades"}},
               [snap(5, 0)], [], 500)
    assert r == {}


def test_ventas_del_dia_de_la_foto_base_no_se_cuentan():
    # La foto base es del día 1; vender ese día no suma (conservador).
    assert estimar_ingreso(0, [snap(1, 20)], [("2026-10-01", 20)], dt(1, 13)) == -20
    assert estimar_ingreso(0, [snap(1, 20)], [("2026-10-02", 20)], dt(1, 13)) == 0


if __name__ == "__main__":
    pruebas = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for f in pruebas:
        f()
        print("ok  ", f.__name__)
    print(f"{len(pruebas)} pruebas ok")
