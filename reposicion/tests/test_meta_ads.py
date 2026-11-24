"""Pruebas de reposicion/meta_ads_sync.py (lógica pura, sin red ni base de datos).

Correr:  python3 -m reposicion.tests.test_meta_ads
"""

from reposicion.meta_ads_sync import (agrupar_anuncios, agrupar_productos, destino_creativo, filas_guardar, mapa_skus,
                                      sku_de_producto, _compra)


def test_compra_prefiere_omni_y_cae_a_las_otras():
    assert _compra([{"action_type": "link_click", "value": "9"}, {"action_type": "purchase", "value": "2"}, {"action_type": "omni_purchase", "value": "3"}]) == 3
    assert _compra([{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "4"}]) == 4
    assert _compra(None) == 0 and _compra([{"action_type": "link_click", "value": "9"}]) == 0


def test_sku_de_producto_toma_lo_que_va_antes_de_la_coma():
    assert sku_de_producto("0019-1almendra, Acolchado Símil Plumón") == "0019-1ALMENDRA"
    assert sku_de_producto("000363") == "000363"
    assert sku_de_producto(None) == "" and sku_de_producto("None, algo") == ""


def test_mapa_skus_no_asigna_los_repetidos():
    prods = [{"product_id": 1, "variantes": [{"id": 11, "sku": "A"}, {"id": 12, "sku": "b"}]},
             {"product_id": 2, "variantes": [{"id": 21, "sku": "B"}, {"id": 22, "sku": ""}]}]
    mapa, repetidos = mapa_skus(prods)
    assert mapa["A"] == 1 and mapa["11"] == 1 and mapa["22"] == 2 and "B" not in mapa and repetidos == {"B"}


def test_agrupar_anuncios_descarta_lo_que_no_gasto():
    filas = [{"date_start": "2026-11-20", "ad_id": "9", "ad_name": "x", "campaign_name": "C", "spend": "100.456", "impressions": "1000", "clicks": "20",
              "actions": [{"action_type": "omni_purchase", "value": "2"}], "action_values": [{"action_type": "omni_purchase", "value": "50000"}]},
             {"date_start": "2026-11-20", "ad_id": "8", "spend": "0"}]
    r = agrupar_anuncios(filas)
    assert list(r) == [("2026-11-20", "9")]
    assert r[("2026-11-20", "9")] == {"campana": "C", "anuncio": "x", "gasto": 100.46, "impresiones": 1000, "clics": 20, "compras": 2, "valor": 50000.0}


def test_agrupar_productos_suma_anuncios_del_mismo_sku_y_dia():
    filas = [{"date_start": "2026-11-20", "product_id": "A, Sábanas", "spend": "10", "impressions": "5", "clicks": "1"},
             {"date_start": "2026-11-20", "product_id": "a, Sábanas", "spend": "5.5", "impressions": "3", "clicks": "0",
              "actions": [{"action_type": "purchase", "value": "1"}], "action_values": [{"action_type": "purchase", "value": "9000"}]},
             {"date_start": "2026-11-20", "product_id": "ZZ, Otro", "spend": "2"},
             {"date_start": "2026-11-20", "product_id": "A, Sábanas", "spend": "0"}]
    r = agrupar_productos(filas, {"A": 1})
    assert r[("2026-11-20", "A")] == {"product_id": 1, "gasto": 15.5, "impresiones": 8, "clics": 1, "compras": 1, "valor": 9000.0}
    assert r[("2026-11-20", "ZZ")]["product_id"] is None


def test_destino_creativo_producto_categoria_y_catalogo():
    link = lambda u: {"object_story_spec": {"link_data": {"link": u}}}
    assert destino_creativo(link("https://www.pretahome.com/productos/sabanas-x/?utm_source=ig")) == (False, "/productos/sabanas-x")
    assert destino_creativo(link("https://www.pretahome.com/dormitorio/cubrecamas1/")) == (False, "/dormitorio/cubrecamas1")
    assert destino_creativo(link("https://www.pretahome.com/")) == (False, "/")
    assert destino_creativo(link("https://www.instagram.com/p/abc")) == (False, None)
    assert destino_creativo({"product_set_id": "123"}) == (True, None)
    assert destino_creativo({"object_story_spec": {"template_data": {"link": "https://www.pretahome.com/"}}}) == (True, "/")


def test_destino_creativo_varias_paginas_queda_sin_destino():
    c = {"object_story_spec": {"link_data": {"child_attachments": [{"link": "https://www.pretahome.com/productos/a/"}, {"link": "https://www.pretahome.com/productos/b/"}]}}}
    assert destino_creativo(c) == (False, None)
    c2 = {"asset_feed_spec": {"link_urls": [{"website_url": "https://www.pretahome.com/cortinas/largas/"}, {"website_url": "https://www.pretahome.com/cortinas/largas"}]}}
    assert destino_creativo(c2) == (False, "/cortinas/largas")


def test_filas_guardar_une_anuncio_con_su_destino():
    anuncios = {("2026-11-20", "9"): {"campana": "C", "anuncio": "x", "gasto": 10.0, "impresiones": 1, "clics": 0, "compras": 0, "valor": 0.0}}
    fa, fp = filas_guardar(anuncios, {("2026-11-20", "A"): {"product_id": 1, "gasto": 5.0, "impresiones": 1, "clics": 0, "compras": 0, "valor": 0.0}},
                           {"9": {"object_story_spec": {"link_data": {"link": "https://www.pretahome.com/productos/z/"}}}})
    assert fa[0]["pagina"] == "/productos/z" and fa[0]["catalogo"] is False and fa[0]["ad_id"] == "9"
    assert fp[0]["sku"] == "A" and fp[0]["product_id"] == 1


if __name__ == "__main__":
    for nombre, fn in list(globals().items()):
        if nombre.startswith("test_"):
            fn()
            print("ok", nombre)
