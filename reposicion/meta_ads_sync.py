#!/usr/bin/env python3
"""
reposicion/meta_ads_sync.py — gasto de Meta Ads (Instagram y Facebook) por producto y por página de destino.

Lee con la API de Meta (solo lectura, token META_ADS_TOKEN de un usuario del sistema con permiso ads_read) la cuenta
publicitaria de Pret a Home y guarda en Supabase:
  · mkt_meta_anuncio_dia: gasto, clics y compras de cada anuncio por día, y a qué página de la tienda lleva
    (se lee del creativo del anuncio; los de catálogo no tienen una página fija).
  · mkt_meta_producto_dia: en los anuncios de catálogo, Meta reparte el gasto por producto del catálogo (su id es el SKU);
    se cruza con las variantes de mkt_producto para saber de qué producto de la tienda es.

Sin --aplicar solo muestra lo que guardaría (y no necesita las tablas). Uso:
  python3 -m reposicion.meta_ads_sync [--dias 8] [--aplicar]
"""

import argparse
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta

import requests

from reposicion import core, db
from reposicion.mkt_sync import pagina_normal
from reposicion.scorecard_tn_sync import AR

CUENTA = "act_1647230420018605"          # «Preta Prueba MP»: la cuenta publicitaria real de Pret a Home
API = "https://graph.facebook.com/v21.0"
ACCIONES_COMPRA = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")   # en orden de preferencia
CAMPOS = "ad_id,ad_name,campaign_name,spend,impressions,clicks,actions,action_values"
DOMINIO_TIENDA = "pretahome"


# ── Meta ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def _cabeceras():
    token = os.environ.get("META_ADS_TOKEN", "").strip()
    if not token:
        raise SystemExit("Falta el secret META_ADS_TOKEN en el entorno.")
    return {"Authorization": f"Bearer {token}"}      # nunca va en la URL: así no queda en ningún mensaje de error


def _pedir(url, params=None, reintentos=4):
    """GET con reintentos si Meta limita el ritmo o falla un momento. Los errores muestran solo el cuerpo que devuelve Meta."""
    for intento in range(reintentos):
        r = requests.get(url, headers=_cabeceras(), params=params, timeout=120)
        if r.status_code == 200:
            return r.json()
        try:
            codigo = r.json().get("error", {}).get("code")
        except ValueError:
            codigo = None
        # Código 100 = pedido mal armado: reintentar no lo arregla (Meta a veces lo acompaña con un 500).
        pasajero = codigo in (1, 2, 4, 17, 32, 613) or (r.status_code >= 500 and codigo != 100)
        if pasajero and intento < reintentos - 1:
            time.sleep(20 * (intento + 1))
            continue
        raise RuntimeError(f"Meta respondió {r.status_code}: {r.text[:400]}")


def paginar(ruta, params):
    """Todas las filas de un reporte de Meta, siguiendo la paginación."""
    filas, url, p = [], f"{API}/{ruta}", params
    while url:
        j = _pedir(url, p)
        filas.extend(j.get("data", []))
        url, p = (j.get("paging") or {}).get("next"), None
    return filas


def insights(desde, hasta, extra=None):
    params = {"level": "ad", "time_increment": 1, "fields": CAMPOS, "limit": 500,
              "time_range": '{"since":"%s","until":"%s"}' % (desde, hasta), **(extra or {})}
    return paginar(f"{CUENTA}/insights", params)


def creativos(ad_ids):
    """{ad_id: creativo}, un pedido por anuncio (el parámetro `ids` para leer varios juntos ya no existe en las versiones nuevas de la API)."""
    out = {}
    for ad in sorted(ad_ids):
        try:
            out[ad] = _pedir(f"{API}/{ad}", {"fields": "creative{product_set_id,object_story_spec,asset_feed_spec}"}).get("creative") or {}
        except RuntimeError as e:                    # un anuncio borrado no debe frenar la carga de los demás
            core.tnlog(f"  ⚠ no pude leer el creativo del anuncio {ad}: {str(e)[:160]}")
            out[ad] = {}
    return out


# ── Lógica pura (con pruebas: python3 -m reposicion.tests.test_meta_ads) ────────────────────────────────────────

def _compra(lista):
    """Valor de la acción de compra en una lista `actions` o `action_values` de Meta (0 si no hay)."""
    por = {a.get("action_type"): float(a.get("value") or 0) for a in lista or []}
    return next((por[t] for t in ACCIONES_COMPRA if t in por), 0.0)


def sku_de_producto(texto):
    """El breakdown product_id de Meta viene como «SKU, nombre del producto»: devuelve el SKU en mayúsculas ('' si no hay)."""
    sku = str(texto or "").split(",", 1)[0].strip().upper()
    return "" if sku in ("", "NONE", "NULL") else sku


def mapa_skus(productos):
    """({sku o id de variante → product_id}, {skus repetidos en más de un producto}).
    `productos`: filas de mkt_producto con product_id y variantes [{id, sku, …}]. Un SKU repetido no se asigna a ninguno."""
    quien = defaultdict(set)
    for p in productos:
        for v in p.get("variantes") or []:
            for clave in (str(v.get("sku") or "").strip().upper(), str(v.get("id") or "")):
                if clave:
                    quien[clave].add(p["product_id"])
    return {k: next(iter(s)) for k, s in quien.items() if len(s) == 1}, {k for k, s in quien.items() if len(s) > 1}


def agrupar_anuncios(filas):
    """{(fecha, ad_id): {…}} con gasto, impresiones, clics, compras y valor de cada anuncio por día (solo los que gastaron)."""
    out = {}
    for f in filas:
        gasto = float(f.get("spend") or 0)
        if gasto <= 0:
            continue
        out[(f["date_start"], f["ad_id"])] = {
            "campana": f.get("campaign_name") or "", "anuncio": f.get("ad_name") or "", "gasto": round(gasto, 2),
            "impresiones": int(f.get("impressions") or 0), "clics": int(f.get("clicks") or 0),
            "compras": int(_compra(f.get("actions"))), "valor": round(_compra(f.get("action_values")), 2)}
    return out


def agrupar_productos(filas, sku_a_producto):
    """{(fecha, sku): {…}} sumando los anuncios de catálogo. product_id queda en None si el SKU no se pudo asignar a un producto."""
    out = {}
    for f in filas:
        sku, gasto = sku_de_producto(f.get("product_id")), float(f.get("spend") or 0)
        if not sku or gasto <= 0:
            continue
        v = out.setdefault((f["date_start"], sku), {"product_id": sku_a_producto.get(sku), "gasto": 0.0, "impresiones": 0, "clics": 0, "compras": 0, "valor": 0.0})
        v["gasto"] += gasto
        v["impresiones"] += int(f.get("impressions") or 0)
        v["clics"] += int(f.get("clicks") or 0)
        v["compras"] += int(_compra(f.get("actions")))
        v["valor"] += _compra(f.get("action_values"))
    for v in out.values():
        v["gasto"], v["valor"] = round(v["gasto"], 2), round(v["valor"], 2)
    return out


def desglose_por_anuncio(filas):
    """{(fecha, ad_id): {gasto, impresiones, clics, compras, valor}} de lo que Meta repartió por producto del catálogo en cada anuncio."""
    out = {}
    for f in filas:
        gasto = float(f.get("spend") or 0)
        if not sku_de_producto(f.get("product_id")) or gasto <= 0:
            continue
        v = out.setdefault((f["date_start"], f["ad_id"]), {"gasto": 0.0, "impresiones": 0, "clics": 0, "compras": 0, "valor": 0.0})
        v["gasto"] += gasto
        v["impresiones"] += int(f.get("impressions") or 0)
        v["clics"] += int(f.get("clicks") or 0)
        v["compras"] += int(_compra(f.get("actions")))
        v["valor"] += _compra(f.get("action_values"))
    return out


def destino_creativo(creativo):
    """(es_de_catalogo, página de la tienda a la que lleva o None). Si el anuncio lleva a varias páginas distintas, None."""
    spec = creativo.get("object_story_spec") or {}
    ld, vd, td = spec.get("link_data") or {}, spec.get("video_data") or {}, spec.get("template_data") or {}
    links = [ld.get("link"), ((ld.get("call_to_action") or {}).get("value") or {}).get("link"),
             ((vd.get("call_to_action") or {}).get("value") or {}).get("link"), td.get("link")]
    links += [c.get("link") for c in ld.get("child_attachments") or []]
    links += [u.get("website_url") for u in (creativo.get("asset_feed_spec") or {}).get("link_urls") or []]
    paginas = {pagina_normal(l) for l in links if l and DOMINIO_TIENDA in l.lower()}
    catalogo = bool(creativo.get("product_set_id")) or bool(td)
    return catalogo, (next(iter(paginas)) if len(paginas) == 1 else None)


def resto_anuncio(v, d):
    """Lo del anuncio `v` que Meta NO repartió por producto (`d`, puede ser None): nunca negativo."""
    d = d or {"gasto": 0.0, "impresiones": 0, "clics": 0, "compras": 0, "valor": 0.0}
    return {"gasto": round(max(0.0, v["gasto"] - d["gasto"]), 2), "impresiones": max(0, v["impresiones"] - d["impresiones"]),
            "clics": max(0, v["clics"] - d["clics"]), "compras": max(0, v["compras"] - d["compras"]), "valor": round(max(0.0, v["valor"] - d["valor"]), 2)}


def filas_guardar(anuncios, productos, creativos_por_ad, desglose=None):
    """(filas de mkt_meta_anuncio_dia, filas de mkt_meta_producto_dia).
    Cada peso se cuenta una sola vez: lo que Meta reparte por producto va a mkt_meta_producto_dia y de cada anuncio solo se guarda
    el resto (con la página a la que lleva el anuncio, si se sabe). Un anuncio de catálogo puro casi no deja resto."""
    dest = {ad: destino_creativo(c)[1] for ad, c in creativos_por_ad.items()}
    desglose = desglose or {}
    fa = []
    for (f, ad), v in sorted(anuncios.items()):
        r = resto_anuncio(v, desglose.get((f, ad)))
        if r["gasto"] >= 1:                                           # menos de $1: es redondeo entre las dos consultas
            fa.append({"fecha": f, "ad_id": ad, "campana": v["campana"], "anuncio": v["anuncio"], "pagina": dest.get(ad), **r})
    fp = [{"fecha": f, "sku": sku, **v} for (f, sku), v in sorted(productos.items())]
    return fa, fp


# ── Programa principal ───────────────────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=8, help="días hacia atrás que se recalculan (hoy incluido)")
    ap.add_argument("--aplicar", action="store_true", help="guardar en Supabase (sin esto solo muestra)")
    args = ap.parse_args()
    config = db.load_config()
    hoy = datetime.now(AR).date()
    desde = hoy - timedelta(days=args.dias - 1)

    core.tnlog(f"Meta Ads {desde} → {hoy}: gasto por anuncio…")
    filas_ad = insights(desde, hoy)
    anuncios = agrupar_anuncios(filas_ad)
    core.tnlog(f"  {len({a for _, a in anuncios})} anuncios con gasto, ${sum(v['gasto'] for v in anuncios.values()):,.0f} en total")

    core.tnlog("Creativos (a qué página lleva cada anuncio)…")
    creativos_por_ad = creativos({a for _, a in anuncios})
    destinos = {ad: destino_creativo(c) for ad, c in creativos_por_ad.items()}

    core.tnlog("Gasto de catálogo por producto…")
    t0 = time.time()
    filas_prod = insights(desde, hoy, {"breakdowns": "product_id"})
    core.tnlog(f"  {len(filas_prod)} filas del desglose por producto en {time.time() - t0:.0f} s")
    desglose = desglose_por_anuncio(filas_prod)
    prods = db.select(config, "mkt_producto", {"select": "product_id,variantes", "order": "product_id"})
    sku_a_producto, repetidos = mapa_skus(prods)
    productos = agrupar_productos(filas_prod, sku_a_producto)
    fa, fp = filas_guardar(anuncios, productos, creativos_por_ad, desglose)

    total = sum(v["gasto"] for v in anuncios.values())
    por_prod = sum(v["gasto"] for v in productos.values())
    con_pagina = sum(r["gasto"] for r in fa if r["pagina"])
    sin_destino = sum(r["gasto"] for r in fa if not r["pagina"])
    asignado = sum(v["gasto"] for v in productos.values() if v["product_id"] is not None)
    pct = lambda a, b: f"{(a / b * 100 if b else 0):.0f} %"
    core.tnlog(f"Reparto del gasto de ${total:,.0f}: por producto del catálogo ${por_prod:,.0f} ({pct(por_prod, total)}) · páginas conocidas ${con_pagina:,.0f} "
               f"({pct(con_pagina, total)}) · sin destino ${sin_destino:,.0f} ({pct(sin_destino, total)})")
    core.tnlog(f"Catálogo: {len({s for _, s in productos})} SKUs; asignado a un producto de la tienda: ${asignado:,.0f} ({pct(asignado, por_prod)})")
    sin_asignar = defaultdict(float)
    for (_, sku), v in productos.items():
        if v["product_id"] is None:
            sin_asignar[sku] += v["gasto"]
    por_ad = defaultdict(lambda: {"gasto": 0.0, "producto": 0.0, "resto": 0.0, "campana": "", "pagina": None})
    for (f, ad), v in anuncios.items():
        a = por_ad[ad]
        a["gasto"] += v["gasto"]
        a["producto"] += (desglose.get((f, ad)) or {}).get("gasto", 0.0)
        a["campana"], a["pagina"] = v["campana"], destinos.get(ad, (False, None))[1]
    for r in fa:
        por_ad[r["ad_id"]]["resto"] += r["gasto"]
    core.tnlog("Anuncios con más gasto (total · por producto · resto a la página · página):")
    for ad, a in sorted(por_ad.items(), key=lambda kv: -kv[1]["gasto"])[:8]:
        core.tnlog(f"  {ad}: ${a['gasto']:,.0f} · ${a['producto']:,.0f} · ${a['resto']:,.0f} · {a['pagina']} · {a['campana'][:34]}")
    for sku, g in sorted(sin_asignar.items(), key=lambda kv: -kv[1])[:5]:
        core.tnlog(f"  sin producto · SKU {sku}: ${g:,.0f}{' (repetido en más de un producto)' if sku in repetidos else ' (no está en la tienda)'}")
    top = defaultdict(float)
    for (_, sku), v in productos.items():
        if v["product_id"] is not None:
            top[v["product_id"]] += v["gasto"]
    nombres = {p["product_id"]: p for p in prods}
    for pid, g in sorted(top.items(), key=lambda kv: -kv[1])[:5]:
        core.tnlog(f"  producto {pid}: ${g:,.0f}")
    por_pagina = defaultdict(float)
    for r in fa:
        if r["pagina"]:
            por_pagina[r["pagina"]] += r["gasto"]
    for pg, g in sorted(por_pagina.items(), key=lambda kv: -kv[1])[:5]:
        core.tnlog(f"  página {pg}: ${g:,.0f}")
    del nombres

    if not args.aplicar:
        core.tnlog(f"(simulación) guardaría {len(fa)} filas de anuncios y {len(fp)} de productos")
        return
    try:                                                    # las tablas llegan con su propia migración (mkt_meta_gasto)
        db.upsert(config, "mkt_meta_anuncio_dia", fa, on_conflict="fecha,ad_id")
        db.upsert(config, "mkt_meta_producto_dia", fp, on_conflict="fecha,sku")
    except RuntimeError as e:
        raise SystemExit(f"No pude guardar (¿falta aplicar la migración mkt_meta_gasto?): {str(e)[:300]}")
    core.tnlog(f"✓ guardado: {len(fa)} filas de anuncios y {len(fp)} de productos")


if __name__ == "__main__":
    main()
