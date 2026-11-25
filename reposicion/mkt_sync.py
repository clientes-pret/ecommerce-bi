#!/usr/bin/env python3
"""
reposicion/mkt_sync.py — datos de la plataforma de Marketing (Tienda Nube Pret a Home), por producto.

Cada corrida deja en Supabase, por producto de Tienda Nube (cada página de producto):
- mkt_producto       = catálogo vigente: nombre, link (handle), categoría, variantes con precio / promo / costo / stock.
- mkt_producto_dia   = por día (hora de Argentina): visitas (GA4), unidades y pedidos (órdenes pagas no canceladas
                       de Tienda Nube), stock, precio de lista, precio final y costo. De acá sale también el historial de precios.
- mkt_producto_hora  = visitas y unidades por hora de las últimas 48 hs (para el "últimas 24 hs").
- mkt_homepage       = qué productos están hoy en la homepage pública y desde cuándo.

Visitas = GA4 `itemsViewed` por variante (itemId = id de variante de Tienda Nube, verificado 2.939 coincidencias),
sumadas por producto. Si el token de GA4 no sirve (vencido), el resto se guarda igual y las visitas quedan en NULL
con un aviso: nunca se inventa un número.

Los últimos días se recalculan cada vez (un reembolso o una cancelación posterior cambia un día ya cargado).

Uso: python3 -m reposicion.mkt_sync                  (simula: muestra lo que guardaría, no escribe)
     python3 -m reposicion.mkt_sync --aplicar        (guarda)
     python3 -m reposicion.mkt_sync --dias 14 --aplicar
Pruebas: python3 -m reposicion.tests.test_mkt
"""

import argparse
import json
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from reposicion import core, db
from reposicion.scorecard_tn_sync import AR, traer_ordenes

CANAL = "tn_pret"
GA4_PROPERTY = "376963177"                   # propiedad de GA4 de Pret a Home
HOME_URL = "https://www.pretahome.com/"
ROOT = Path(__file__).parent.parent


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _txt(campo):
    """Los textos de Tienda Nube vienen como {'es': '...'}."""
    if isinstance(campo, dict):
        return campo.get("es") or next((v for v in campo.values() if v), "") or ""
    return campo or ""


# ── Catálogo de Tienda Nube ──────────────────────────────────────────────────────────────────────────────────────

def resumir_producto(p):
    """Una fila de mkt_producto + el resumen que va a mkt_producto_dia (precio y costo de la variante con más stock)."""
    variantes = []
    for v in p.get("variants") or []:
        precio = _num(v.get("price"))
        promo = _num(v.get("promotional_price"))
        variantes.append({
            "id": v.get("id"), "sku": v.get("sku"), "precio": precio,
            "promo": promo if 0 < promo < precio else None,
            "costo": _num(v.get("cost")) or None,
            "stock": v.get("stock") if v.get("stock") is not None else 0,
            "nombre": " / ".join(_txt(x) for x in (v.get("values") or []) if _txt(x)),
        })
    # La variante representativa es la que más stock tiene (si no hay stock en ninguna, la primera).
    rep = max(variantes, key=lambda x: x["stock"]) if variantes else None
    cats = [_txt(c.get("name")) for c in (p.get("categories") or [])]
    imgs = p.get("images") or []
    fila = {
        "product_id": p["id"], "handle": _txt(p.get("handle")), "nombre": _txt(p.get("name")),
        "categoria": cats[0] if cats else None, "marca": p.get("brand") or None,
        "publicado": bool(p.get("published", True)), "imagen": (imgs[0].get("src") if imgs else None),
        "variantes": variantes, "actualizado_at": datetime.now(timezone.utc).isoformat(),
    }
    resumen = {
        "stock": sum(v["stock"] for v in variantes),
        "precio_lista": rep["precio"] if rep else None,
        "precio_final": (rep["promo"] or rep["precio"]) if rep else None,
        "costo": rep["costo"] if rep else None,
    }
    return fila, resumen


# ── Ventas de Tienda Nube ────────────────────────────────────────────────────────────────────────────────────────

def agregar_ordenes(ordenes):
    """({(product_id, fecha AR): {unidades, pedidos, ingresos}}, {(product_id, hora AR ISO): unidades}).
    Solo órdenes pagas y no canceladas (como el Scorecard)."""
    dia = defaultdict(lambda: {"unidades": 0, "pedidos": 0, "ingresos": 0.0})
    hora = defaultdict(int)
    for o in ordenes:
        if o.get("status") == "cancelled" or o.get("payment_status") != "paid":
            continue
        creada = datetime.fromisoformat(o["created_at"].replace("+0000", "+00:00")).astimezone(AR)
        fecha, h = creada.date().isoformat(), creada.replace(minute=0, second=0, microsecond=0).isoformat()
        vistos = set()
        for l in o.get("products") or []:
            pid = l.get("product_id")
            if pid is None:
                continue
            q = int(_num(l.get("quantity")))
            d = dia[(pid, fecha)]
            d["unidades"] += q
            d["ingresos"] += _num(l.get("price")) * q
            if pid not in vistos:                      # un pedido con dos variantes del mismo producto cuenta una vez
                d["pedidos"] += 1
                vistos.add(pid)
            hora[(pid, h)] += q
    return dia, hora


# ── Visitas (GA4) ────────────────────────────────────────────────────────────────────────────────────────────────

def ga4_access_token():
    """Access token de GA4 desde GA4_TOKEN_JSON (Actions) o data/ga4_token.json (local). None si no sirve."""
    raw = os.environ.get("GA4_TOKEN_JSON", "").strip()
    try:
        tok = json.loads(raw) if raw else json.loads((ROOT / "data" / "ga4_token.json").read_text())
    except (OSError, ValueError):
        core.tnlog("  ⚠ GA4: no encontré el token (GA4_TOKEN_JSON o data/ga4_token.json). Visitas sin cargar.")
        return None
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": tok.get("client_id"), "client_secret": tok.get("client_secret"),
        "refresh_token": tok.get("refresh_token"), "grant_type": "refresh_token"})
    if r.status_code != 200:
        core.tnlog(f"  ⚠ GA4: el token no sirve ({r.json().get('error', r.status_code)}). Hay que volver a autorizar la cuenta de Google: "
                   "python3 reautenticar_ga4.py. Visitas sin cargar.")
        return None
    return r.json()["access_token"]


def ga4_reporte(token, desde, hasta, dimensiones, metricas, extra=None):
    """Filas de un reporte de GA4 (dicts dimensión/métrica -> valor), paginado."""
    filas, offset = [], 0
    while True:
        r = requests.post(f"https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY}:runReport", timeout=120,
                          headers={"Authorization": f"Bearer {token}"}, json={
                              "dateRanges": [{"startDate": desde.isoformat(), "endDate": hasta.isoformat()}],
                              "dimensions": [{"name": d} for d in dimensiones], "metrics": [{"name": m} for m in metricas],
                              "limit": 100000, "offset": offset, **(extra or {})})
        r.raise_for_status()
        j = r.json()
        dh = [h["name"] for h in j.get("dimensionHeaders", [])]
        mh = [h["name"] for h in j.get("metricHeaders", [])]
        for row in j.get("rows", []):
            fila = {dh[i]: v["value"] for i, v in enumerate(row.get("dimensionValues", []))}
            fila.update({mh[i]: v["value"] for i, v in enumerate(row.get("metricValues", []))})
            filas.append(fila)
        offset += 100000
        if offset >= int(j.get("rowCount", 0)):
            return filas


def visitas_por_producto(filas, variante_a_producto, clave_tiempo):
    """{(product_id, tiempo): visitas} sumando las variantes de cada producto. Variantes que no conocemos se ignoran."""
    out = defaultdict(int)
    for f in filas:
        pid = variante_a_producto.get(str(f.get("itemId")))
        if pid is None:
            continue
        out[(pid, clave_tiempo(f))] += int(_num(f.get("itemsViewed")))
    return out


def _fecha_ga4(f):
    d = f["date"]
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def _hora_ga4(f):
    d = f["dateHour"]                                   # AAAAMMDDHH, en la zona horaria de la propiedad (Argentina)
    return datetime(int(d[:4]), int(d[4:6]), int(d[6:8]), int(d[8:10]), tzinfo=AR).isoformat()


# ── Anuncios: visitas y compras por fuente (GA4) ─────────────────────────────────────────────────────────────────

FUENTES_META = ("ig", "fb", "an", "facebook", "instagram")      # `sessionSource` de los anuncios de Meta (medio "paid")


def fuente_de(source, medium):
    """'meta' si la sesión vino de un anuncio de Meta (Instagram/Facebook/Audience Network, medio pagado), 'google' si vino de
    un anuncio de Google Ads (google / cpc), None para todo lo demás (orgánico, directo, mail, referidos…)."""
    source, medium = (source or "").lower(), (medium or "").lower()
    if source in FUENTES_META and medium == "paid":
        return "meta"
    if source == "google" and medium == "cpc":
        return "google"
    return None


def _filtro(campo, valores):
    return {"filter": {"fieldName": campo, "inListFilter": {"values": list(valores)}}}


# Filtro de GA4: solo sesiones de anuncios de Meta o de Google Ads (el resto no interesa y haría el reporte enorme).
FILTRO_ANUNCIOS = {"dimensionFilter": {"orGroup": {"expressions": [
    {"andGroup": {"expressions": [_filtro("sessionSource", FUENTES_META), _filtro("sessionMedium", ["paid"])]}},
    {"andGroup": {"expressions": [_filtro("sessionSource", ["google"]), _filtro("sessionMedium", ["cpc"])]}},
]}}}


def pagina_normal(ruta):
    """Ruta de la página de destino sin parámetros, sin barra final y en minúsculas ('/' queda como '/')."""
    r = (ruta or "").split("?")[0].split("#")[0].strip().lower()
    if r.startswith("http"):
        r = "/" + r.split("//", 1)[-1].split("/", 1)[-1] if "/" in r.split("//", 1)[-1] else "/"
    r = r.rstrip("/")
    return r if r else "/"


def atribucion_producto(filas, variante_a_producto):
    """{(product_id, fecha, fuente): [visitas, compras]} sumando las variantes de cada producto.
    `filas` trae date, itemId, sessionSource, sessionMedium, itemsViewed, itemsPurchased."""
    out = defaultdict(lambda: [0, 0])
    for f in filas:
        pid = variante_a_producto.get(str(f.get("itemId")))
        fuente = fuente_de(f.get("sessionSource"), f.get("sessionMedium"))
        if pid is None or fuente is None:
            continue
        v = out[(pid, _fecha_ga4(f), fuente)]
        v[0] += int(_num(f.get("itemsViewed")))
        v[1] += int(_num(f.get("itemsPurchased")))
    return out


def atribucion_paginas(filas):
    """{(fecha, página, fuente): [sesiones, compras]}. Páginas que GA4 no pudo identificar ('(not set)') se descartan."""
    out = defaultdict(lambda: [0, 0])
    for f in filas:
        fuente = fuente_de(f.get("sessionSource"), f.get("sessionMedium"))
        pagina = pagina_normal(f.get("landingPage"))
        if fuente is None or "(not set)" in pagina:
            continue
        v = out[(_fecha_ga4(f), pagina, fuente)]
        v[0] += int(_num(f.get("sessions")))
        v[1] += int(_num(f.get("ecommercePurchases")))
    return out


def google_campanas(filas):
    """{(fecha, campaña): [gasto, clics, sesiones]} de Google Ads. Las sesiones que no son de una campaña ('(not set)') se descartan."""
    out = defaultdict(lambda: [0.0, 0, 0])
    for f in filas:
        campana = (f.get("sessionGoogleAdsCampaignName") or "").strip()
        if not campana or campana == "(not set)":
            continue
        v = out[(_fecha_ga4(f), campana)]
        v[0] += _num(f.get("advertiserAdCost"))
        v[1] += int(_num(f.get("advertiserAdClicks")))
        v[2] += int(_num(f.get("sessions")))
    return {k: v for k, v in out.items() if v[0] > 0 or v[1] > 0}


def filas_anuncios(prod, pag, goo):
    """Lo que se guarda en las tablas mkt_producto_fuente_dia, mkt_pagina_fuente_dia y mkt_google_campana_dia."""
    return (
        [{"fecha": f, "product_id": p, "fuente": fu, "visitas": v[0], "compras": v[1]} for (p, f, fu), v in sorted(prod.items())],
        [{"fecha": f, "pagina": pg, "fuente": fu, "sesiones": v[0], "compras": v[1]} for (f, pg, fu), v in sorted(pag.items())],
        [{"fecha": f, "campana": c, "gasto": round(v[0], 2), "clics": v[1], "sesiones": v[2]} for (f, c), v in sorted(goo.items())],
    )


# ── Tráfico y conversión por canal (GA4) ─────────────────────────────────────────────────────────────────────────

METRICAS_TRAFICO = ["sessions", "engagedSessions", "newUsers", "addToCarts", "checkouts", "ecommercePurchases", "purchaseRevenue"]
# Dos puntos de vista: la fuente de la sesión en la que se compró (último contacto) y la fuente por la que el usuario llegó
# la primera vez (primer contacto). Cada uno trae sus dimensiones de GA4.
MODELOS_TRAFICO = {"sesion": ("sessionSource", "sessionMedium"), "primer": ("firstUserSource", "firstUserMedium")}


def agrupar_trafico(filas, modelo):
    """{(fecha, modelo, fuente, medio): [sesiones, activas, nuevos, carritos, checkouts, compras, ingresos]}.
    Fuente y medio se guardan tal cual los trae GA4 (el canal se decide al leer, en la base)."""
    dim_fuente, dim_medio = MODELOS_TRAFICO[modelo]
    out = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0.0])
    for f in filas:
        fuente = (f.get(dim_fuente) or "").strip() or "(not set)"
        medio = (f.get(dim_medio) or "").strip() or "(not set)"
        v = out[(_fecha_ga4(f), modelo, fuente, medio)]
        v[0] += int(_num(f.get("sessions")))
        v[1] += int(_num(f.get("engagedSessions")))
        v[2] += int(_num(f.get("newUsers")))
        v[3] += int(_num(f.get("addToCarts")))
        v[4] += int(_num(f.get("checkouts")))
        v[5] += int(_num(f.get("ecommercePurchases")))
        v[6] += _num(f.get("purchaseRevenue"))
    return {k: v for k, v in out.items() if any(v)}


def filas_trafico(trafico):
    return [{"fecha": f, "modelo": mo, "fuente": fu, "medio": me, "sesiones": v[0], "sesiones_activas": v[1], "usuarios_nuevos": v[2],
             "carritos": v[3], "checkouts": v[4], "compras": v[5], "ingresos": round(v[6], 2)}
            for (f, mo, fu, me), v in sorted(trafico.items())]


def traer_trafico(token, desde, hasta):
    trafico = {}
    for modelo, dims in MODELOS_TRAFICO.items():
        trafico.update(agrupar_trafico(ga4_reporte(token, desde, hasta, ["date", *dims], METRICAS_TRAFICO), modelo))
    return trafico


# ── Homepage ─────────────────────────────────────────────────────────────────────────────────────────────────────

def handles_homepage(html):
    """Handles de los productos linkeados en el HTML de la homepage, en orden de aparición y sin repetir."""
    vistos, out = set(), []
    for h in re.findall(r"pretahome\.com/productos/([a-z0-9][a-z0-9-]*)", html, flags=re.I):
        h = h.lower()
        if h not in vistos:
            vistos.add(h)
            out.append(h)
    return out


def traer_homepage():
    r = requests.get(HOME_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0 (compatible; PretAHomeBI)"})
    r.raise_for_status()
    return handles_homepage(r.text)


# ── Armado de filas ──────────────────────────────────────────────────────────────────────────────────────────────

def filas_dia(productos, ventas_dia, visitas_dia, hoy, dias):
    """Una fila por producto y día de la ventana. Stock, precio y costo son los de hoy (la base guarda el historial día a día).
    visitas None = GA4 no disponible (no es lo mismo que 0 visitas)."""
    filas = []
    for pid, (_, resumen) in productos.items():
        for k in range(dias):
            f = (hoy - timedelta(days=k)).isoformat()
            v = ventas_dia.get((pid, f), {"unidades": 0, "pedidos": 0, "ingresos": 0.0})
            fila = {"fecha": f, "product_id": pid, "unidades": v["unidades"], "pedidos": v["pedidos"],
                    "ingresos": round(v["ingresos"], 2)}
            if visitas_dia is not None:                 # sin GA4 no se manda la clave: así no se pisan visitas ya guardadas
                fila["visitas"] = visitas_dia.get((pid, f), 0)
            if k == 0:                                  # el estado actual solo se guarda en el día de hoy
                fila.update(stock=resumen["stock"], precio_lista=resumen["precio_lista"],
                            precio_final=resumen["precio_final"], costo=resumen["costo"])
            filas.append(fila)
    return filas


RECIENTES = 8      # días que siempre se guardan completos (el tablero suma 7 días: una fila faltante contaría como "sin visitas")


def filas_utiles(filas, hoy, recientes=RECIENTES):
    """Para no guardar cientos de miles de filas vacías al cargar historia: los últimos `recientes` días van completos
    (incluso con 0) y los más viejos solo si hubo visitas o ventas."""
    limite = (hoy - timedelta(days=recientes - 1)).isoformat()
    return [f for f in filas if f["fecha"] >= limite or f["unidades"] > 0 or (f.get("visitas") or 0) > 0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=8, help="días hacia atrás que se recalculan (hoy incluido)")
    ap.add_argument("--aplicar", action="store_true", help="guardar en Supabase (sin esto solo muestra)")
    args = ap.parse_args()
    config = db.load_config()
    cfg = config["channels"][CANAL]
    hoy = datetime.now(AR).date()
    desde = hoy - timedelta(days=args.dias - 1)

    core.tnlog(f"Catálogo de Tienda Nube ({cfg.get('label', CANAL)})…")
    crudos = core.tn_get_products(cfg)
    productos = {p["id"]: resumir_producto(p) for p in crudos}
    variante_a_producto = {str(v["id"]): p["id"] for p in crudos for v in (p.get("variants") or [])}
    core.tnlog(f"  {len(productos)} productos, {len(variante_a_producto)} variantes")

    core.tnlog(f"Órdenes pagas {desde} → {hoy}…")
    ventas_dia, ventas_hora = agregar_ordenes(traer_ordenes(cfg, desde, hoy))
    core.tnlog(f"  {sum(v['unidades'] for v in ventas_dia.values())} unidades en {len({k[0] for k in ventas_dia})} productos")

    visitas_dia = visitas_hora = None
    ads_prod = ads_pag = ads_goo = trafico = None
    token = ga4_access_token()
    if token:
        core.tnlog("Visitas de GA4…")
        visitas_dia = visitas_por_producto(ga4_reporte(token, desde, hoy, ["date", "itemId"], ["itemsViewed"]),
                                           variante_a_producto, _fecha_ga4)
        visitas_hora = visitas_por_producto(ga4_reporte(token, hoy - timedelta(days=2), hoy, ["dateHour", "itemId"], ["itemsViewed"]),
                                            variante_a_producto, _hora_ga4)
        core.tnlog(f"  {sum(visitas_dia.values())} visitas de producto en {len({k[0] for k in visitas_dia})} productos")
        core.tnlog("Anuncios (Meta y Google Ads) por producto y página de destino…")
        d_item = ["date", "itemId", "sessionSource", "sessionMedium"]
        ads_prod = atribucion_producto(ga4_reporte(token, desde, hoy, d_item, ["itemsViewed", "itemsPurchased"], FILTRO_ANUNCIOS), variante_a_producto)
        ads_pag = atribucion_paginas(ga4_reporte(token, desde, hoy, ["date", "landingPage", "sessionSource", "sessionMedium"], ["sessions", "ecommercePurchases"], FILTRO_ANUNCIOS))
        ads_goo = google_campanas(ga4_reporte(token, desde, hoy, ["date", "sessionGoogleAdsCampaignName"], ["advertiserAdCost", "advertiserAdClicks", "sessions"]))
        core.tnlog(f"  {len(ads_prod)} filas por producto, {len(ads_pag)} por página, {len(ads_goo)} de campañas de Google Ads "
                   f"(gasto de Google Ads: ${sum(v[0] for v in ads_goo.values()):,.0f})")
        core.tnlog("Tráfico por canal (último y primer contacto)…")
        trafico = traer_trafico(token, desde, hoy)
        core.tnlog(f"  {len(trafico)} filas, {sum(v[0] for k, v in trafico.items() if k[1] == 'sesion')} sesiones, "
                   f"{sum(v[5] for k, v in trafico.items() if k[1] == 'sesion')} compras (último contacto)")

    handle_a_producto = {p["handle"].lower(): pid for pid, (p, _) in productos.items() if p["handle"]}
    home = [handle_a_producto[h] for h in traer_homepage() if h in handle_a_producto]
    core.tnlog(f"Homepage: {len(home)} productos de la tienda")

    catalogo = [fila for fila, _ in productos.values()]
    dia = filas_utiles(filas_dia(productos, ventas_dia, visitas_dia, hoy, args.dias), hoy)
    horas = []
    for (pid, h) in set(ventas_hora) | set(visitas_hora or {}):
        fila = {"hora": h, "product_id": pid, "unidades": ventas_hora.get((pid, h), 0)}
        if visitas_hora is not None:
            fila["visitas"] = visitas_hora.get((pid, h), 0)
        horas.append(fila)
    if not args.aplicar:
        ayer = [f for f in dia if f["fecha"] == (hoy - timedelta(days=1)).isoformat()]
        for f in sorted(ayer, key=lambda f: (-(f.get("visitas") or 0), -f["unidades"]))[:5]:
            core.tnlog(f"  ayer · {productos[f['product_id']][0]['nombre'][:50]}: visitas {f.get('visitas', 'sin GA4')} · unidades {f['unidades']}")
        if ads_prod is not None:
            fp, fg, fc = filas_anuncios(ads_prod, ads_pag, ads_goo)
            top = defaultdict(lambda: [0, 0])
            for r in fp:
                if r["fecha"] >= (hoy - timedelta(days=7)).isoformat():
                    top[(r["product_id"], r["fuente"])][0] += r["visitas"]
                    top[(r["product_id"], r["fuente"])][1] += r["compras"]
            for (pid, fu), v in sorted(top.items(), key=lambda kv: -kv[1][0])[:5]:
                core.tnlog(f"  anuncios {fu} · {productos[pid][0]['nombre'][:46]}: visitas {v[0]} · compras {v[1]}")
            core.tnlog(f"  (anuncios) guardaría {len(fp)} filas por producto, {len(fg)} por página y {len(fc)} de campañas")
        if trafico is not None:
            core.tnlog(f"  (tráfico) guardaría {len(trafico)} filas por día, fuente y medio")
        core.tnlog(f"(simulación) guardaría {len(catalogo)} productos, {len(dia)} filas por día, {len(horas)} por hora y {len(home)} en homepage")
        return

    db.upsert(config, "mkt_producto", catalogo, on_conflict="product_id")
    # Dos envíos con las mismas columnas cada uno: si se mezclaran, PostgREST completaría con vacío lo que falte y pisaría
    # el stock, el precio y el costo de los días anteriores.
    db.upsert(config, "mkt_producto_dia", [f for f in dia if "stock" in f], on_conflict="fecha,product_id")
    db.upsert(config, "mkt_producto_dia", [f for f in dia if "stock" not in f], on_conflict="fecha,product_id")
    db.upsert(config, "mkt_producto_hora", horas, on_conflict="hora,product_id")
    db.delete(config, "mkt_producto_hora", {"hora": "lt." + (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()})   # solo se guardan ~48 hs
    previos = {r["product_id"]: r for r in db.select(config, "mkt_homepage", {"select": "product_id,desde,ultimo", "order": "product_id"})}
    ahora = []
    for pid in home:
        p = previos.get(pid)
        sigue = p and date.fromisoformat(p["ultimo"]) >= hoy - timedelta(days=2)   # estuvo ayer o antes de ayer: no se cortó la racha
        ahora.append({"product_id": pid, "desde": p["desde"] if sigue else hoy.isoformat(), "ultimo": hoy.isoformat()})
    db.upsert(config, "mkt_homepage", ahora, on_conflict="product_id")
    if ads_prod is not None:
        fp, fg, fc = filas_anuncios(ads_prod, ads_pag, ads_goo)
        try:                                                  # las tablas de anuncios llegan con su propia migración
            for tabla, filas, pk in (("mkt_producto_fuente_dia", fp, "fecha,product_id,fuente"), ("mkt_pagina_fuente_dia", fg, "fecha,pagina,fuente"),
                                     ("mkt_google_campana_dia", fc, "fecha,campana")):
                db.upsert(config, tabla, filas, on_conflict=pk)
            core.tnlog(f"✓ anuncios: {len(fp)} filas por producto, {len(fg)} por página, {len(fc)} de campañas")
        except RuntimeError as e:
            core.tnlog(f"  ⚠ no pude guardar los anuncios (¿falta aplicar la migración mkt_anuncios?): {str(e)[:200]}")
    if trafico is not None:
        try:                                                  # la tabla de tráfico llega con su propia migración
            db.upsert(config, "mkt_trafico_dia", filas_trafico(trafico), on_conflict="fecha,modelo,fuente,medio")
            core.tnlog(f"✓ tráfico: {len(trafico)} filas por día, fuente y medio")
        except RuntimeError as e:
            core.tnlog(f"  ⚠ no pude guardar el tráfico (¿falta aplicar la migración mkt_trafico?): {str(e)[:200]}")
    core.tnlog(f"✓ guardado: {len(catalogo)} productos, {len(dia)} filas por día, {len(horas)} por hora, {len(ahora)} en homepage")


if __name__ == "__main__":
    main()
