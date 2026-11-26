#!/usr/bin/env python3
"""
reposicion/ml_control_sync.py — control diario de Mercado Libre (Pret a Home y Casa Laván) para la pantalla
operaciones/mercadolibre.html. Solo LEE de Mercado Libre: no contesta reclamos ni toca publicaciones.

Cada corrida deja en Supabase, por cuenta:
- mlc_resumen_dia = la foto del día (la última corrida del día pisa a las anteriores): reputación de 60 días,
                    reclamos abiertos, envíos de la semana a tiempo / demorados y alertas de medidas.
- mlc_reclamos    = reclamos abiertos y los creados en los últimos 60 días, con motivo, producto y vencimiento.
- mlc_envios      = un renglón por envío (Flex, Colecta, etc.) con su fecha límite y si Mercado Libre lo marca demorado.
- mlc_alertas     = alertas de "medidas y peso del paquete mayores a los declarados" (moderaciones del grupo PRECIO).
- mlc_riesgo      = publicaciones activas (no Full) cuyos datos de paquete no cierran: conviene revisarlas antes de
                    que llegue la alerta.

Si una parte falla (Mercado Libre no responde), las demás se guardan igual, esa parte queda sin dato (nunca en 0)
y la corrida termina con error para que se vea en GitHub.

Uso: python3 -m reposicion.ml_control_sync                       (simula: muestra lo que guardaría, no escribe)
     python3 -m reposicion.ml_control_sync --salida foto.json    (simula y además deja todo en un archivo)
     python3 -m reposicion.ml_control_sync --aplicar             (guarda)
     python3 -m reposicion.ml_control_sync --dias-envios 14 --aplicar
Pruebas: python3 -m reposicion.tests.test_ml_control
"""

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from reposicion import core, db

API = "https://api.mercadolibre.com"
AR = timezone(timedelta(hours=-3))
CUENTAS = {"pret": "ml_pret", "lavan": "ml_lavan"}

DIAS_RECLAMOS = 60
DIAS_ENVIOS_INICIAL = 14      # primera carga
DIAS_ENVIOS = 3               # corridas siguientes (más los envíos que todavía no terminaron)
MAS_QUE_DECLARADO = 1.25      # Mercado Libre midió 25 % más (peso o volumen) que lo declarado
DATOS_DISTINTOS = 0.5         # un dato del paquete es menos de la mitad del otro
HILOS = 8

TIPOS_RECLAMO = ("mediations", "returns")      # el resto (cancelaciones de compra, etc.) se guarda sin detalle
ENVIO_TERMINADO = ("delivered", "not_delivered", "cancelled")

MOTIVOS = {
    "repentant_buyer": "No lo quiere (llegó bien)",
    "different_than_published": "Distinto a la publicación",
    "broken_item": "Llegó roto o dañado",
    "different_color_or_size": "Color, tamaño o modelo equivocado",
    "missing_invoice": "No llegó la factura",
    "not_working_item": "No funciona",
    "estimated_delivery": "Pregunta cuándo le llega",
    "different_color_or_size_fashion": "Color, tamaño o modelo equivocado",
    "different_item_other": "Recibió otro producto",
    "fake_different": "No es de la marca original",
    "missing_item": "Faltan productos",
    "missing_accessories": "Faltan accesorios o piezas",
    "empty_box": "La caja llegó vacía",
    "damaged_package_broken_item": "Embalaje y producto dañados",
    "damaged_package_missing_accessories": "Embalaje dañado y faltan piezas",
    "damaged_package_empty_box": "Embalaje dañado y vacío",
    "delivered_but_not_receive_package": "Figura entregado y no lo recibió",
    "undelivered_repentant_buyer": "No lo recibió y se arrepintió",
    "delivery_date_was_not_met": "No llegó en la fecha prometida",
    "respondent_unanswered": "No recibió respuesta nuestra",
    "unauthorized_purchase": "No reconoce la compra",
}


# ── Mercado Libre ────────────────────────────────────────────────────────────────────────────────────────────────

class ErrorML(RuntimeError):
    pass


class Ml:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"
        adaptador = requests.adapters.HTTPAdapter(pool_maxsize=HILOS * 2)
        self.s.mount("https://", adaptador)

    def pedir(self, path, intentos=6, **params):
        """(status, json). Reintenta cortes de red, 429, 403 (el límite de ML a veces responde así) y errores 5xx."""
        status, cuerpo = 0, None
        for i in range(intentos):
            try:
                r = self.s.get(API + path, params=params, timeout=40)
            except requests.RequestException:
                time.sleep(2 * (i + 1))
                continue
            status = r.status_code
            try:
                cuerpo = r.json()
            except ValueError:
                cuerpo = None
            if status in (429, 500, 502, 503, 504) or (status == 403 and i < 2):
                time.sleep(2 * (i + 1))
                continue
            break
        return status, cuerpo

    def get(self, path, **params):
        """El json, o ErrorML si no se pudo leer."""
        status, cuerpo = self.pedir(path, **params)
        if status not in (200, 206) or cuerpo is None:
            raise ErrorML(f"{path} respondió {status}")
        return cuerpo

    def items(self, ids):
        """Publicaciones de a 20. Si un lote falla se piden una por una. Devuelve (lista, cuántas no se pudieron leer)."""
        campos = "id,title,status,sub_status,shipping,attributes,sold_quantity,seller_custom_field"

        def lote(grupo):
            status, d = self.pedir("/items", intentos=3, ids=",".join(grupo), attributes=campos)
            if status == 200 and d is not None:
                return [e["body"] for e in d if e.get("code") == 200], sum(e.get("code") != 200 for e in d)
            buenas, malas = [], 0
            for iid in grupo:
                st, b = self.pedir(f"/items/{iid}", intentos=4)
                if st == 200 and b:
                    buenas.append(b)
                else:
                    malas += 1
            return buenas, malas

        grupos = [ids[i:i + 20] for i in range(0, len(ids), 20)]
        with ThreadPoolExecutor(4) as ex:
            res = list(ex.map(lote, grupos))
        return [b for r, _ in res for b in r], sum(m for _, m in res)


def _paralelo(fn, cosas):
    with ThreadPoolExecutor(HILOS) as ex:
        return list(ex.map(fn, cosas))


# ── Lógica pura (con pruebas) ────────────────────────────────────────────────────────────────────────────────────

def _fecha(texto):
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None


def resumen_reputacion(rep):
    """Reputación de 60 días. Con la cuenta protegida Mercado Libre muestra 0 y deja el valor verdadero en
    `excluded`: se guarda el verdadero y se marca `protegida`."""
    rep = rep or {}
    m = rep.get("metrics") or {}
    protegida = False

    def metrica(nombre):
        nonlocal protegida
        x = m.get(nombre) or {}
        ex = x.get("excluded") or {}
        if "real_value" in ex:
            protegida = True
            return ex.get("real_value"), ex.get("real_rate")
        return x.get("value"), x.get("rate")

    rec, rec_t = metrica("claims")
    dem, dem_t = metrica("delayed_handling_time")
    can, can_t = metrica("cancellations")
    pct = lambda t: None if t is None else round(t * 100, 3)
    return {"nivel": rep.get("level_id"), "medalla": rep.get("power_seller_status"),
            "proteccion_hasta": rep.get("protection_end_date"), "protegida": protegida,
            "ventas_60d": (m.get("sales") or {}).get("completed"),
            "reclamos_60d": rec, "reclamos_tasa": pct(rec_t),
            "demoras_60d": dem, "demoras_tasa": pct(dem_t),
            "cancel_60d": can, "cancel_tasa": pct(can_t)}


def texto_motivo(motivo_id, razon):
    nombre = (razon or {}).get("name")
    if nombre in MOTIVOS:
        return MOTIVOS[nombre]
    if (motivo_id or "").startswith("PNR"):
        return "No recibió el producto"
    return (razon or {}).get("detail") or motivo_id or "Sin motivo"


def fila_reclamo(c, cuenta):
    """Un reclamo de la búsqueda de ML → renglón. `vence` = la respuesta obligatoria nuestra que vence primero."""
    obligatorias = []
    if c.get("status") == "opened":
        for p in c.get("players") or []:
            if p.get("role") in ("respondent",):
                obligatorias += [a for a in p.get("available_actions") or [] if a.get("mandatory") and a.get("due_date")]
    primera = min(obligatorias, key=lambda a: _fecha(a["due_date"]), default=None)
    return {"id": c["id"], "cuenta": cuenta, "tipo": c.get("type"), "etapa": c.get("stage"), "estado": c.get("status"),
            "motivo_id": c.get("reason_id"), "motivo": None, "recurso": c.get("resource"), "recurso_id": c.get("resource_id"),
            "order_id": c.get("resource_id") if c.get("resource") == "order" else None,
            "item_id": None, "titulo": None, "sku": None,
            "creado": c.get("date_created"), "actualizado": c.get("last_updated"),
            "vence": primera["due_date"] if primera else None, "accion": primera["action"] if primera else None,
            "afecta": None, "resolucion": (c.get("resolution") or {}).get("reason")}


def falta_detalle(fila, previa):
    """Si hay que volver a pedirle a ML el detalle (afecta reputación, producto) de un reclamo."""
    if fila["tipo"] not in TIPOS_RECLAMO:
        return False
    if not previa or previa.get("afecta") is None:
        return True
    return _fecha(previa.get("actualizado")) != _fecha(fila["actualizado"]) or previa.get("estado") != fila["estado"]


def item_de_orden(orden):
    lineas = (orden or {}).get("order_items") or []
    if not lineas:
        return {"item_id": None, "titulo": None, "sku": None}
    it = lineas[0].get("item") or {}
    titulo = it.get("title")
    if titulo and len(lineas) > 1:
        titulo += f" (+{len(lineas) - 1} más)"
    return {"item_id": it.get("id"), "titulo": titulo, "sku": it.get("seller_sku") or it.get("seller_custom_field")}


def fila_envio(cuenta, envio_id, envio, sla, demoras, datos):
    logistica = envio.get("logistic_type")
    estado = envio.get("status")
    return {"id": envio_id, "cuenta": cuenta, "logistica": logistica,
            "order_id": datos.get("order_id"), "item_id": datos.get("item_id"), "titulo": datos.get("titulo"),
            "sku": datos.get("sku"), "vendido": datos.get("vendido"),
            "limite": (sla or {}).get("expected_date"), "sla": (sla or {}).get("status"),
            "demoras": sorted({d.get("type") for d in (demoras or {}).get("delays", []) if d.get("type")}),
            "estado": estado, "subestado": envio.get("substatus"),
            # Full lo despacha Mercado Libre: se guarda una vez para no volver a preguntarlo y no cuenta.
            "final": logistica == "fulfillment" or estado in ENVIO_TERMINADO}


def _iso(momento):
    """Fecha para un filtro de la base (en UTC y sin microsegundos)."""
    return momento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lunes_de(ahora):
    d = ahora.astimezone(AR)
    return (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def semana_envios(filas, logistica, ahora):
    """(envíos que ya llegaron a su fecha límite desde el lunes, cuántos de esos marca ML como demorados).
    Un envío demorado cuenta aunque después se haya cancelado."""
    desde = lunes_de(ahora)
    total = demorados = 0
    for f in filas:
        lim = _fecha(f.get("limite"))
        if f.get("logistica") != logistica or not lim or lim < desde or f.get("sla") not in ("on_time", "early", "delayed"):
            continue
        if f["sla"] == "delayed":
            total += 1
            demorados += 1
        elif lim <= ahora and f.get("estado") != "cancelled":
            total += 1
    return total, demorados


def _medida(texto):
    """'25 cm' → 25.0 (cm) · '1.5 kg' → 1500.0 (g)."""
    m = re.match(r"\s*([\d.]+)\s*([a-zA-Z]+)", texto or "")
    if not m:
        return None
    return float(m[1]) * {"kg": 1000, "mm": 0.1, "m": 100}.get(m[2].lower(), 1)


def _atributos(item):
    return {a["id"]: a.get("value_name") for a in item.get("attributes") or []}


def paquete(atributos, prefijo):
    """[alto, ancho, largo, peso] o None si falta alguno."""
    v = [_medida(atributos.get(f"{prefijo}{k}")) for k in ("HEIGHT", "WIDTH", "LENGTH", "WEIGHT")]
    return None if any(x is None or x <= 0 for x in v) else v


def texto_paquete(p):
    if not p:
        return None
    n = lambda x: f"{x:g}".replace(".", ",")
    peso = f"{n(round(p[3] / 1000, 2))} kg" if p[3] >= 1000 else f"{n(round(p[3]))} g"
    return f"{n(p[0])} × {n(p[1])} × {n(p[2])} cm · {peso}"


def sku_item(item):
    return _atributos(item).get("SELLER_SKU") or item.get("seller_custom_field")


def link_corregir(item_id):
    return f"https://www.mercadolibre.com.ar/syi/core/modify?itemId={item_id}&taskId=pyme_package_task"


def riesgo_medidas(item):
    """None, o el motivo por el que conviene revisar los datos de paquete de una publicación activa."""
    logistica = (item.get("shipping") or {}).get("logistic_type")
    if item.get("status") != "active" or logistica == "fulfillment":
        return None
    at = _atributos(item)
    declarado, otro = paquete(at, "SELLER_PACKAGE_"), paquete(at, "PACKAGE_")
    if not declarado or not otro:
        return None
    vol = lambda p: p[0] * p[1] * p[2]
    dif_vol, dif_peso = vol(otro) / vol(declarado), otro[3] / declarado[3]
    fuente = at.get("PACKAGE_DATA_SOURCE")
    if fuente in ("MEASUREMENT", "CERTIFIED_MEASUREMENT") and max(dif_vol, dif_peso) > MAS_QUE_DECLARADO:
        tipo = "ml_midio_mas"
    elif fuente == "SELLER" and min(dif_vol, dif_peso) < DATOS_DISTINTOS:
        tipo = "datos_distintos"
    else:
        return None
    return {"item_id": item["id"], "titulo": item.get("title"), "sku": sku_item(item), "tipo": tipo,
            "declarado": texto_paquete(declarado), "otro": texto_paquete(otro),
            "dif_peso": round(dif_peso, 2), "dif_volumen": round(dif_vol, 2),
            "vendidos": item.get("sold_quantity") or 0, "logistica": logistica, "link": link_corregir(item["id"])}


def filas_alertas(cuenta, infracciones, items):
    """Un renglón por alerta. El estado de la publicación es el de hoy; `pendiente` solo en la alerta más nueva de
    una publicación que Mercado Libre tiene frenada esperando la corrección."""
    por_id = {b["id"]: b for b in items}
    ultima = {}
    for i in sorted(infracciones, key=lambda i: _fecha(i["date_created"])):
        ultima[i["related_item_id"]] = i["id"]
    filas = []
    for i in infracciones:
        iid = i["related_item_id"]
        b = por_id.get(iid)
        at = _atributos(b or {})
        sub = (b or {}).get("sub_status") or []
        filas.append({
            "id": str(i["id"]), "cuenta": cuenta, "item_id": iid, "fecha": i["date_created"],
            "motivo": re.sub(r"<[^>]+>", " ", i.get("reason") or "").strip(),
            "reincidencia": "nueva irregularidad" in (i.get("reason") or ""),
            "titulo": (b or {}).get("title"), "sku": sku_item(b) if b else None,
            "estado_item": (b or {}).get("status"), "subestado_item": sub,
            "pendiente": bool(b) and ultima[iid] == i["id"] and b.get("status") == "under_review" and "waiting_for_patch" in sub,
            "declarado": texto_paquete(paquete(at, "SELLER_PACKAGE_")), "paquete": texto_paquete(paquete(at, "PACKAGE_")),
            "link": link_corregir(iid)})
    return filas


# ── Lectura de cada parte ────────────────────────────────────────────────────────────────────────────────────────

def _pagina_reclamos(ml, estado, offset):
    return ml.get("/post-purchase/v1/claims/search", status=estado, limit=50, offset=offset, sort="date_created:desc")


def traer_reclamos(ml, cuenta, previos, ahora):
    desde = ahora - timedelta(days=DIAS_RECLAMOS)
    crudos = {}
    for estado in ("opened", "closed"):
        offset = 0
        while True:
            d = _pagina_reclamos(ml, estado, offset)
            datos = d.get("data") or []
            for c in datos:
                if estado == "opened" or _fecha(c["date_created"]) >= desde:
                    crudos[c["id"]] = c
            offset += len(datos)
            total = (d.get("paging") or {}).get("total", 0)
            viejo = estado == "closed" and datos and _fecha(datos[-1]["date_created"]) < desde
            if not datos or offset >= total or viejo:
                break
    # Los que estaban abiertos y son demasiado viejos para venir en los cerrados: se piden de a uno.
    desaparecidos = []
    for rid, p in previos.items():
        if p.get("estado") == "opened" and rid not in crudos:
            st, c = ml.pedir(f"/post-purchase/v1/claims/{rid}")
            if st == 200 and c:
                crudos[rid] = c
            elif st == 404:          # Mercado Libre ya no lo tiene: deja de figurar abierto
                desaparecidos.append(p)
            else:
                raise ErrorML(f"no se pudo leer el reclamo {rid} ({st})")

    filas = [fila_reclamo(c, cuenta) for c in crudos.values()]
    razones = {}
    for rid in {f["motivo_id"] for f in filas if f["motivo_id"] and f["tipo"] in TIPOS_RECLAMO}:
        st, d = ml.pedir(f"/post-purchase/v1/claims/reasons/{rid}")
        razones[rid] = d if st == 200 else None

    def detalle(f):
        previa = previos.get(f["id"])
        if f["tipo"] in TIPOS_RECLAMO:
            f["motivo"] = texto_motivo(f["motivo_id"], razones.get(f["motivo_id"]))
        if previa:
            for k in ("order_id", "item_id", "titulo", "sku", "afecta"):
                f[k] = f[k] or previa.get(k)
        if not falta_detalle(f, previa):
            return f
        st, d = ml.pedir(f"/post-purchase/v1/claims/{f['id']}/affects-reputation")
        if st == 200 and d:
            f["afecta"] = d.get("affects_reputation")
        if not f["item_id"]:
            oid = f["order_id"]
            if not oid and f["recurso"] == "shipment":
                st, envio = ml.pedir(f"/shipments/{f['recurso_id']}")
                oid = envio.get("order_id") if st == 200 and envio else None
            if oid:
                st, orden = ml.pedir(f"/orders/{oid}")
                f["order_id"] = oid
                if st == 200:
                    f.update(item_de_orden(orden))
        return f

    filas = _paralelo(detalle, filas)
    for f in filas:
        f.pop("recurso_id", None)
    for p in desaparecidos:
        columnas = fila_reclamo({"id": p["id"]}, cuenta).keys() - {"recurso_id"}
        filas.append({**{k: p.get(k) for k in columnas}, "estado": "closed", "vence": None, "accion": None})
    return filas


def traer_envios(ml, cuenta, user_id, previos, dias, ahora):
    datos = {}
    for atras in range(dias, -1, -1):
        dia = (ahora.astimezone(AR) - timedelta(days=atras)).strftime("%Y-%m-%d")
        offset = 0
        while True:
            d = ml.get("/orders/search", seller=user_id, sort="date_asc", limit=50, offset=offset,
                       **{"order.date_created.from": f"{dia}T00:00:00.000-03:00",
                          "order.date_created.to": f"{dia}T23:59:59.999-03:00"})
            res = d.get("results") or []
            for o in res:
                sid = (o.get("shipping") or {}).get("id")
                if sid and sid not in datos:
                    datos[sid] = {"order_id": o["id"], "vendido": o.get("date_created"), **item_de_orden(o)}
            offset += len(res)
            if not res or offset >= (d.get("paging") or {}).get("total", 0):
                break
    limite_viejo = ahora - timedelta(days=21)
    pedir = {sid for sid in datos if not (previos.get(sid) or {}).get("final")}
    for sid, p in previos.items():
        vendido = _fecha(p.get("vendido"))
        if not p.get("final") and (not vendido or vendido >= limite_viejo):
            pedir.add(sid)
            datos.setdefault(sid, {k: p.get(k) for k in ("order_id", "item_id", "titulo", "sku", "vendido")})

    fallos = []

    def uno(sid):
        st, envio = ml.pedir(f"/shipments/{sid}")
        if st != 200 or not envio:
            fallos.append(sid)
            return None
        sla = demoras = None
        if envio.get("logistic_type") != "fulfillment":
            st, sla = ml.pedir(f"/shipments/{sid}/sla")
            if st != 200:
                sla = None
                if st != 404:
                    fallos.append(sid)
                    return None
            if sla and sla.get("status") == "delayed":
                st, demoras = ml.pedir(f"/shipments/{sid}/delays")
                demoras = demoras if st == 200 else None
        return fila_envio(cuenta, sid, envio, sla, demoras, datos[sid])

    filas = [f for f in _paralelo(uno, sorted(pedir)) if f]
    if len(fallos) > max(5, len(pedir) // 50):
        raise ErrorML(f"no se pudieron leer {len(fallos)} de {len(pedir)} envíos")
    if fallos:
        core.tnlog(f"  ⚠ {len(fallos)} envíos sin leer (se reintentan en la próxima corrida)")
    return filas


def traer_alertas(ml, cuenta, user_id):
    infracciones, offset = [], 0
    while True:
        d = ml.get(f"/moderations/infractions/{user_id}", limit=20, offset=offset, filter_subgroup="PRECIO")
        lote = d.get("infractions") or []
        infracciones += [i for i in lote if str(i.get("related_item_id", "")).startswith("MLA")]
        offset += 20
        if not lote or offset >= (d.get("paging") or {}).get("total", 0):
            break
    items, malas = ml.items(sorted({i["related_item_id"] for i in infracciones}))
    if malas:
        raise ErrorML(f"no se pudo leer el estado de {malas} publicaciones con alerta")
    return filas_alertas(cuenta, infracciones, items)


def traer_riesgo(ml, cuenta, user_id):
    ids, scroll = [], None
    while True:
        params = {"search_type": "scan", "limit": 100, "status": "active"}
        if scroll:
            params["scroll_id"] = scroll
        d = ml.get(f"/users/{user_id}/items/search", **params)
        if not d.get("results"):
            break
        ids += d["results"]
        scroll = d.get("scroll_id")
    ids = list(dict.fromkeys(ids))
    items, malas = ml.items(ids)
    if malas > max(5, len(ids) // 100):
        raise ErrorML(f"no se pudieron leer {malas} de {len(ids)} publicaciones")
    filas = [{**r, "cuenta": cuenta} for r in (riesgo_medidas(b) for b in items) if r]
    return filas, malas == 0


# ── Base de datos ────────────────────────────────────────────────────────────────────────────────────────────────

def _previos(config, tabla, cuenta, filtros):
    try:
        filas = db.select(config, tabla, {"select": "*", "cuenta": f"eq.{cuenta}", "order": "id", **filtros})
    except Exception as e:  # tabla todavía sin crear, o sin base en una simulación
        core.tnlog(f"  ⚠ no pude leer {tabla}: {str(e)[:150]}")
        return None
    return {f["id"]: f for f in filas}


def correr_cuenta(config, cuenta, aplicar, dias_envios, ahora):
    cfg = config["channels"][CUENTAS[cuenta]]
    ml = Ml(core.ml_ensure_token(CUENTAS[cuenta], cfg))
    user_id = cfg["user_id"]
    sello = ahora.isoformat()
    salida, errores = {}, []
    resumen = {"fecha": ahora.astimezone(AR).strftime("%Y-%m-%d"), "cuenta": cuenta, "actualizado_at": sello}

    def parte(nombre, fn):
        try:
            core.tnlog(f"  {cfg['label']}: {nombre}…")
            fn()
        except Exception as e:
            errores.append(f"{cfg['label']} · {nombre}: {e}")
            core.tnlog(f"  ✗ {errores[-1]}")

    def guardar(tabla, filas, conflicto):
        salida[tabla] = filas
        if aplicar:
            db.upsert(config, tabla, [{**f, "actualizado_at": sello} if tabla != "mlc_reclamos" else {**f, "visto_at": sello}
                                      for f in filas], conflicto)

    def reputacion():
        resumen.update(resumen_reputacion(ml.get(f"/users/{user_id}").get("seller_reputation")))

    def reclamos():
        desde = _iso(ahora - timedelta(days=DIAS_RECLAMOS + 5))
        previos = _previos(config, "mlc_reclamos", cuenta, {"or": f"(estado.eq.opened,creado.gte.{desde})"})
        if previos is None and aplicar:
            raise RuntimeError("no se pudo leer lo ya guardado")
        filas = traer_reclamos(ml, cuenta, previos or {}, ahora)
        guardar("mlc_reclamos", filas, "id")
        abiertos = [f for f in filas if f["estado"] == "opened" and f["tipo"] in TIPOS_RECLAMO]
        resumen.update(reclamos_abiertos=len(abiertos),
                       reclamos_afectan=sum(f["afecta"] == "affected" for f in abiertos),
                       reclamos_responder=sum(bool(f["vence"]) for f in abiertos))

    def envios():
        desde = _iso(ahora - timedelta(days=22))
        previos = _previos(config, "mlc_envios", cuenta, {"or": f"(final.is.false,vendido.gte.{desde})"})
        if previos is None and aplicar:
            raise RuntimeError("no se pudo leer lo ya guardado")
        dias = dias_envios or (DIAS_ENVIOS if previos else DIAS_ENVIOS_INICIAL)
        filas = traer_envios(ml, cuenta, user_id, previos or {}, dias, ahora)
        guardar("mlc_envios", filas, "id")
        todas = {**(previos or {}), **{f["id"]: f for f in filas}}.values()
        resumen["flex_semana"], resumen["flex_demorados"] = semana_envios(todas, "self_service", ahora)
        resumen["colecta_semana"], resumen["colecta_demorados"] = semana_envios(todas, "cross_docking", ahora)

    def alertas():
        filas = traer_alertas(ml, cuenta, user_id)
        guardar("mlc_alertas", filas, "id")
        if aplicar:   # una alerta que Mercado Libre ya no devuelve no puede quedar como pendiente
            db.patch(config, "mlc_alertas", {"cuenta": f"eq.{cuenta}", "pendiente": "is.true", "actualizado_at": f"lt.{sello}"},
                     {"pendiente": False})
        hace30 = ahora - timedelta(days=30)
        resumen.update(alertas_pendientes=sum(f["pendiente"] for f in filas),
                       alertas_30d=sum(_fecha(f["fecha"]) >= hace30 for f in filas))

    def riesgo():
        filas, completo = traer_riesgo(ml, cuenta, user_id)
        guardar("mlc_riesgo", filas, "item_id")
        resumen["riesgo_medidas"] = len(filas)
        if aplicar and completo:   # lo que ya no está en riesgo se borra, solo si se leyó todo
            db.delete(config, "mlc_riesgo", {"cuenta": f"eq.{cuenta}", "actualizado_at": f"lt.{sello}"})

    parte("reputación", reputacion)
    parte("reclamos", reclamos)
    parte("peso y medidas (alertas)", alertas)
    parte("envíos", envios)
    parte("peso y medidas (revisión de publicaciones)", riesgo)

    salida["mlc_resumen_dia"] = [resumen]
    if aplicar:
        # PostgREST pisa solo las columnas que se mandan: una parte que falló conserva lo de la corrida anterior de hoy.
        db.upsert(config, "mlc_resumen_dia", [resumen], "fecha,cuenta")
    return salida, errores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="guardar en Supabase (sin esto solo muestra)")
    ap.add_argument("--salida", help="dejar todo lo leído en un archivo JSON")
    ap.add_argument("--dias-envios", type=int, help="días de ventas a repasar para los envíos")
    ap.add_argument("--cuenta", choices=list(CUENTAS), help="solo una cuenta")
    args = ap.parse_args()
    config = db.load_config()
    ahora = datetime.now(timezone.utc)
    todo, errores = {}, []
    for cuenta in [args.cuenta] if args.cuenta else CUENTAS:
        salida, e = correr_cuenta(config, cuenta, args.aplicar, args.dias_envios, ahora)
        errores += e
        for tabla, filas in salida.items():
            todo.setdefault(tabla, []).extend(filas)
    # Los números del negocio se muestran solo al simular: el registro de GitHub Actions de este repo es público.
    for r in todo.get("mlc_resumen_dia", []) if not args.aplicar else []:
        print(json.dumps(r, ensure_ascii=False))
    print({t: len(f) for t, f in todo.items()})
    if args.salida:
        with open(args.salida, "w") as f:
            json.dump(todo, f, ensure_ascii=False)
        print(f"Guardado en {args.salida}")
    if not args.aplicar:
        print("Simulación: no se guardó nada en la base (usar --aplicar).")
    if errores:
        print("\n".join(["Partes con error:"] + errores))
        sys.exit(1)


if __name__ == "__main__":
    main()
