"""Pruebas de reposicion/mkt_sync.py (lógica pura, sin red ni base de datos).

Correr:  python3 -m reposicion.tests.test_mkt
"""

from datetime import date

from reposicion.mkt_sync import (agregar_ordenes, filas_dia, handles_homepage, resumir_producto,
                                 visitas_por_producto, _fecha_ga4, _hora_ga4)


def producto(pid=1, variantes=None):
    return {"id": pid, "handle": {"es": "sabanas-x"}, "name": {"es": "Sábanas X"}, "published": True,
            "categories": [{"name": {"es": "Sábanas"}}], "images": [{"src": "http://img/1.jpg"}],
            "variants": variantes if variantes is not None else [
                {"id": 11, "sku": "A", "price": "96000.00", "promotional_price": "81600.00", "cost": "49500.00", "stock": 5,
                 "values": [{"es": "Queen"}]},
                {"id": 12, "sku": "B", "price": "90000.00", "promotional_price": None, "cost": "47000.00", "stock": 40,
                 "values": [{"es": "King"}]}]}


def orden(numero, creada, lineas, estado="paid", status="open"):
    return {"number": numero, "created_at": creada, "payment_status": estado, "status": status,
            "products": [{"product_id": p, "variant_id": v, "quantity": q, "price": str(pr)} for p, v, q, pr in lineas]}


def test_resumen_usa_la_variante_con_mas_stock():
    fila, r = resumir_producto(producto())
    assert fila["handle"] == "sabanas-x" and fila["nombre"] == "Sábanas X" and fila["categoria"] == "Sábanas"
    assert r["stock"] == 45                                        # suma de las variantes de ESTA página
    assert (r["precio_lista"], r["precio_final"], r["costo"]) == (90000.0, 90000.0, 47000.0)   # King: más stock, sin promo


def test_promo_invalida_no_cuenta():
    # promo igual o mayor que el precio = sin promo (como lee el resto del repo)
    p = producto(variantes=[{"id": 1, "sku": "A", "price": "100", "promotional_price": "100", "cost": "50", "stock": 1, "values": []},
                            {"id": 2, "sku": "B", "price": "100", "promotional_price": "120", "cost": "50", "stock": 0, "values": []}])
    fila, r = resumir_producto(p)
    assert r["precio_final"] == 100.0 and all(v["promo"] is None for v in fila["variantes"])


def test_stock_ilimitado_y_sin_costo():
    p = producto(variantes=[{"id": 1, "sku": "A", "price": "100", "promotional_price": None, "cost": None, "stock": None, "values": []}])
    fila, r = resumir_producto(p)
    assert r["stock"] == 0 and r["costo"] is None


def test_solo_pagadas_no_canceladas_y_pedido_unico_por_producto():
    ords = [orden(1, "2026-11-20T15:00:00+0000", [(1, 11, 2, 100), (1, 12, 1, 90), (2, 21, 1, 50)]),
            orden(2, "2026-11-20T16:00:00+0000", [(1, 11, 5, 100)], estado="refunded"),
            orden(3, "2026-11-20T17:00:00+0000", [(1, 11, 5, 100)], estado="paid", status="cancelled")]
    dia, hora = agregar_ordenes(ords)
    assert dia[(1, "2026-11-20")] == {"unidades": 3, "pedidos": 1, "ingresos": 290.0}   # dos variantes, un pedido
    assert dia[(2, "2026-11-20")]["unidades"] == 1


def test_dia_y_hora_en_horario_argentino():
    # 02:30 UTC del 21 = 23:30 del 20 en Argentina
    dia, hora = agregar_ordenes([orden(1, "2026-11-21T02:30:00+0000", [(1, 11, 1, 100)])])
    assert (1, "2026-11-20") in dia and (1, "2026-11-21") not in dia
    assert list(hora)[0][1].startswith("2026-11-20T23:00:00")


def test_visitas_suman_variantes_e_ignoran_las_desconocidas():
    filas = [{"date": "20261120", "itemId": "11", "itemsViewed": "30"}, {"date": "20261120", "itemId": "12", "itemsViewed": "12"},
             {"date": "20261120", "itemId": "999", "itemsViewed": "500"}, {"date": "20261121", "itemId": "11", "itemsViewed": "7"}]
    v = visitas_por_producto(filas, {"11": 1, "12": 1}, _fecha_ga4)
    assert v == {(1, "2026-11-20"): 42, (1, "2026-11-21"): 7}


def test_hora_de_ga4():
    assert _hora_ga4({"dateHour": "2026112015"}) == "2026-11-20T15:00:00-03:00"


def test_homepage_en_orden_sin_repetir_y_sin_el_listado():
    html = ('<a href="https://www.pretahome.com/productos/bowl-15-cm-carol">x</a> <a href="https://www.pretahome.com/productos/">todos</a> '
            '<a href="https://www.pretahome.com/productos/almohada-70x40/">y</a> <a href="https://www.pretahome.com/productos/bowl-15-cm-carol/">dup</a>')
    assert handles_homepage(html) == ["bowl-15-cm-carol", "almohada-70x40"]


def test_filas_dia_no_mandan_visitas_si_ga4_no_estuvo():
    prods = {1: (None, {"stock": 45, "precio_lista": 90000.0, "precio_final": 81600.0, "costo": 47000.0})}
    hoy = date(2026, 11, 24)
    sin = filas_dia(prods, {}, None, hoy, 3)
    assert all("visitas" not in f for f in sin)                    # así la base no pisa lo que ya tenía
    con = filas_dia(prods, {(1, "2026-11-24"): {"unidades": 2, "pedidos": 1, "ingresos": 10.0}}, {(1, "2026-11-23"): 9}, hoy, 3)
    assert [f["visitas"] for f in con] == [0, 9, 0] and con[0]["unidades"] == 2
    assert "stock" in con[0] and "stock" not in con[1]             # el estado actual solo en el día de hoy


def test_historia_vieja_solo_si_hubo_actividad():
    from reposicion.mkt_sync import filas_utiles
    hoy = date(2026, 11, 24)
    filas = [{"fecha": "2026-11-24", "unidades": 0}, {"fecha": "2026-11-17", "unidades": 0, "visitas": 0},   # recientes: se guardan aunque estén en 0
             {"fecha": "2026-11-16", "unidades": 0, "visitas": 0}, {"fecha": "2026-11-16", "unidades": 2},   # viejas: solo la que vendió
             {"fecha": "2026-10-01", "unidades": 0, "visitas": 5}, {"fecha": "2026-10-01", "unidades": 0}]
    assert [(f["fecha"], f["unidades"]) for f in filas_utiles(filas, hoy)] == [("2026-11-24", 0), ("2026-11-17", 0), ("2026-11-16", 2), ("2026-10-01", 0)]


def test_fuente_de_anuncios():
    from reposicion.mkt_sync import fuente_de
    assert fuente_de("ig", "paid") == "meta" and fuente_de("FB", "Paid") == "meta" and fuente_de("an", "paid") == "meta"
    assert fuente_de("google", "cpc") == "google"
    # lo orgánico, referido o directo NO es un anuncio
    assert fuente_de("l.instagram.com", "referral") is None and fuente_de("google", "organic") is None
    assert fuente_de("perfit", "email") is None and fuente_de("(direct)", "(none)") is None and fuente_de(None, None) is None


def test_pagina_normal():
    from reposicion.mkt_sync import pagina_normal
    assert pagina_normal("/") == "/" and pagina_normal("") == "/" and pagina_normal(None) == "/"
    assert pagina_normal("/cortinas/largas/") == "/cortinas/largas" == pagina_normal("/Cortinas/Largas?fbclid=abc#x")
    assert pagina_normal("/productos/cover-uma-king-pret-szbmr") == "/productos/cover-uma-king-pret-szbmr"


def test_atribucion_producto_suma_variantes_y_descarta_lo_que_no_es_anuncio():
    from reposicion.mkt_sync import atribucion_producto
    filas = [{"date": "20261120", "itemId": "11", "sessionSource": "ig", "sessionMedium": "paid", "itemsViewed": "30", "itemsPurchased": "1"},
             {"date": "20261120", "itemId": "12", "sessionSource": "ig", "sessionMedium": "paid", "itemsViewed": "12", "itemsPurchased": "0"},
             {"date": "20261120", "itemId": "11", "sessionSource": "google", "sessionMedium": "cpc", "itemsViewed": "5", "itemsPurchased": "2"},
             {"date": "20261120", "itemId": "11", "sessionSource": "google", "sessionMedium": "organic", "itemsViewed": "99", "itemsPurchased": "9"},
             {"date": "20261120", "itemId": "999", "sessionSource": "ig", "sessionMedium": "paid", "itemsViewed": "500", "itemsPurchased": "50"}]
    r = atribucion_producto(filas, {"11": 1, "12": 1})
    assert r == {(1, "2026-11-20", "meta"): [42, 1], (1, "2026-11-20", "google"): [5, 2]}


def test_atribucion_paginas_une_variantes_de_la_misma_url():
    from reposicion.mkt_sync import atribucion_paginas
    filas = [{"date": "20261120", "landingPage": "/cortinas/largas", "sessionSource": "ig", "sessionMedium": "paid", "sessions": "100", "ecommercePurchases": "2"},
             {"date": "20261120", "landingPage": "/cortinas/largas/", "sessionSource": "fb", "sessionMedium": "paid", "sessions": "20", "ecommercePurchases": "0"},   # misma página, otra fuente de Meta
             {"date": "20261120", "landingPage": "/cortinas/largas?utm=1", "sessionSource": "google", "sessionMedium": "cpc", "sessions": "7", "ecommercePurchases": "1"},
             {"date": "20261120", "landingPage": "(not set)", "sessionSource": "ig", "sessionMedium": "paid", "sessions": "50", "ecommercePurchases": "0"},
             {"date": "20261120", "landingPage": "/", "sessionSource": "perfit", "sessionMedium": "email", "sessions": "300", "ecommercePurchases": "5"}]
    r = atribucion_paginas(filas)
    assert r == {("2026-11-20", "/cortinas/largas", "meta"): [120, 2], ("2026-11-20", "/cortinas/largas", "google"): [7, 1]}   # una sola fila por clave (si no, el guardado falla)


def test_google_campanas_sin_gasto_ni_not_set():
    from reposicion.mkt_sync import google_campanas
    filas = [{"date": "20261120", "sessionGoogleAdsCampaignName": "P.Max - Blanquería", "advertiserAdCost": "390810.96", "advertiserAdClicks": "7202", "sessions": "6041"},
             {"date": "20261120", "sessionGoogleAdsCampaignName": "(not set)", "advertiserAdCost": "0", "advertiserAdClicks": "0", "sessions": "12782"},
             {"date": "20261120", "sessionGoogleAdsCampaignName": "P.Max - Rmkt", "advertiserAdCost": "0", "advertiserAdClicks": "0", "sessions": "16"}]
    assert google_campanas(filas) == {("2026-11-20", "P.Max - Blanquería"): [390810.96, 7202, 6041]}


def test_filas_anuncios_formato_de_guardado():
    from reposicion.mkt_sync import filas_anuncios
    fp, fg, fc = filas_anuncios({(1, "2026-11-20", "meta"): [42, 1]}, {("2026-11-20", "/", "meta"): [9, 0]}, {("2026-11-20", "P.Max"): [100.456, 5, 4]})
    assert fp == [{"fecha": "2026-11-20", "product_id": 1, "fuente": "meta", "visitas": 42, "compras": 1}]
    assert fg == [{"fecha": "2026-11-20", "pagina": "/", "fuente": "meta", "sesiones": 9, "compras": 0}]
    assert fc == [{"fecha": "2026-11-20", "campana": "P.Max", "gasto": 100.46, "clics": 5, "sesiones": 4}]


def test_trafico_junta_filas_repetidas_y_deja_fuente_y_medio_crudos():
    from reposicion.mkt_sync import agrupar_trafico, filas_trafico
    filas = [{"date": "20261120", "sessionSource": "ig", "sessionMedium": "paid", "sessions": "100", "engagedSessions": "60", "newUsers": "50",
              "addToCarts": "8", "checkouts": "3", "ecommercePurchases": "2", "purchaseRevenue": "150000.5"},
             {"date": "20261120", "sessionSource": "ig", "sessionMedium": "paid", "sessions": "10", "engagedSessions": "5", "newUsers": "4",
              "addToCarts": "1", "checkouts": "0", "ecommercePurchases": "0", "purchaseRevenue": "0"},
             {"date": "20261120", "sessionSource": "", "sessionMedium": "", "sessions": "0", "engagedSessions": "0", "newUsers": "0",
              "addToCarts": "0", "checkouts": "0", "ecommercePurchases": "0", "purchaseRevenue": "0"}]            # fila vacía: no se guarda
    t = agrupar_trafico(filas, "sesion")
    assert list(t) == [("2026-11-20", "sesion", "ig", "paid")]
    assert t[("2026-11-20", "sesion", "ig", "paid")] == [110, 65, 54, 9, 3, 2, 150000.5]
    assert filas_trafico(t)[0] == {"fecha": "2026-11-20", "modelo": "sesion", "fuente": "ig", "medio": "paid", "sesiones": 110,
                                   "sesiones_activas": 65, "usuarios_nuevos": 54, "carritos": 9, "checkouts": 3, "compras": 2, "ingresos": 150000.5}


def test_trafico_primer_contacto_lee_las_dimensiones_del_primer_usuario():
    from reposicion.mkt_sync import agrupar_trafico
    filas = [{"date": "20261120", "firstUserSource": "google", "firstUserMedium": "organic", "sessions": "5", "ecommercePurchases": "1", "purchaseRevenue": "1000"}]
    assert agrupar_trafico(filas, "primer") == {("2026-11-20", "primer", "google", "organic"): [5, 0, 0, 0, 0, 1, 1000.0]}


if __name__ == "__main__":
    for nombre, fn in list(globals().items()):
        if nombre.startswith("test_"):
            fn()
            print("ok", nombre)
