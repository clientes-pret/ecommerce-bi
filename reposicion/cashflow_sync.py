#!/usr/bin/env python3
"""
reposicion/cashflow_sync.py — datos reales de MercadoPago para el Cashflow del panel de control.

Por cada cuenta MercadoPago (la de Pret a Home y la de Casa Laván, las mismas credenciales que ML):
- Pide el "reporte de dinero liberado" (/v1/account/release_report): cada movimiento de la cuenta con el
  saldo disponible después de él. De ahí salen las liberaciones por día y categoría (cf_liberaciones), las
  salidas individuales (cf_mp_egresos) y el saldo al cierre de cada día (cf_saldos, fuente 'mp_reporte').
  El reporte trae días completos en hora de Argentina; el día de hoy queda parcial y se pisa en la próxima corrida.
- Lo agendado para liberar (/v1/payments/search por money_release_date futura) → cf_liberaciones_pendientes.
Y de Tiendanube, los cobros por medio de pago (cf_tn_medios): lo pagado con Mercado Pago ya entra en la cuenta
MP, así el Cashflow no lo cuenta dos veces en el estimado de Tienda Nube. Pago Nube no tiene API pública.

Uso: python3 -m reposicion.cashflow_sync            (últimos 3 días + pendientes)
     python3 -m reposicion.cashflow_sync --desde 2026-05-01   (carga histórica, un reporte por mes)
"""

import argparse
import csv
import io
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

from reposicion import core, db

MP = "https://api.mercadopago.com"
AR = timezone(timedelta(hours=-3))
CUENTAS = {"ml_pret": "mp_pret", "ml_lavan": "mp_lavan"}
TIENDAS = {"tn_pret": "pret", "tn_lavan": "lavan"}


def _hoy():
    return datetime.now(AR).date()


def _num(s):
    return float(s) if s not in (None, "") else 0.0


# ── Reporte de dinero liberado ─────────────────────────────────────────────

def _pedir_reporte(token, desde, hasta):
    """Pide el reporte de [desde, hasta] (días de Argentina) y espera a que esté listo. Devuelve el CSV."""
    H = {"Authorization": f"Bearer {token}"}
    begin = datetime.combine(desde, datetime.min.time(), AR).astimezone(timezone.utc)
    end = min(datetime.combine(hasta, datetime.max.time(), AR), datetime.now(AR) - timedelta(minutes=5)).astimezone(timezone.utc)
    pedido = datetime.now(timezone.utc)
    cerrado = hasta < _hoy()
    fin_dia = datetime.combine(hasta, datetime.max.time(), AR).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    begin_iso = begin.strftime("%Y-%m-%dT%H:%M:%SZ")

    def pedir():
        nonlocal begin_iso
        for intento in range(3):
            # Si el pedido corta por tiempo, MercadoPago igual puede haber creado el reporte: la búsqueda de abajo lo encuentra.
            try:
                r = requests.post(f"{MP}/v1/account/release_report", headers=H, timeout=120, json={
                    "begin_date": begin_iso, "end_date": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
            except requests.exceptions.RequestException:
                return
            if r.status_code in (200, 201, 202):
                begin_iso = r.json().get("begin_date") or begin_iso
                return
            if intento == 2:
                raise RuntimeError(f"No se pudo pedir el reporte: {r.status_code} {r.text[:300]}")
            time.sleep(20)

    pedir()
    for intento in range(80):
        time.sleep(15)
        if intento in (20, 40):                         # a veces MercadoPago no genera el pedido: se pide de nuevo
            pedir()
        try:                                            # MercadoPago a veces corta la conexión: se reintenta
            lista = requests.get(f"{MP}/v1/account/release_report/list", headers=H, timeout=60)
            if lista.status_code != 200:
                continue
            # Un tramo ya cerrado no cambia: sirve cualquier reporte del MISMO período (inicio y fin) generado después
            # de que terminó. Solo el inicio no alcanza: el 26/10 tomó uno del 1 al 2/10 y borró el resto del tramo.
            # (date_created viene en UTC aunque diga -04:00, por eso se compara contra UTC.)
            creado = lambda x: datetime.fromisoformat(x["date_created"][:19]).replace(tzinfo=timezone.utc)
            candidatos = [x for x in lista.json() if x.get("begin_date") == begin_iso and (
                creado(x) >= pedido - timedelta(minutes=2)
                or (cerrado and x.get("end_date") == fin_dia
                    and datetime.fromisoformat(x["end_date"].replace("Z", "+00:00")) < creado(x)))]
            if not candidatos:
                continue
            archivo = max(candidatos, key=lambda x: x["date_created"])["file_name"]
            d = requests.get(f"{MP}/v1/account/release_report/{archivo}", headers=H, timeout=180)
            if d.status_code == 200 and d.text.startswith("DATE"):
                return d.text
        except requests.exceptions.RequestException:
            continue
    raise RuntimeError(f"El reporte {desde}→{hasta} no estuvo listo a tiempo")


def _descripcion_pago(token, source_id):
    if not source_id.isdigit():
        return "Transferencia o pago desde la cuenta"
    j = core.ml_get(f"{MP}/v1/payments/{source_id}", token)
    return (j or {}).get("description") or "Pago desde la cuenta"


def _categoria(fila, token, cache):
    """Clasifica un movimiento del reporte. None = fila sin efecto en el saldo (marcadores de retiro)."""
    desc, unidad = fila["DESCRIPTION"], fila["BUSINESS_UNIT"]
    if "payout_" in desc:                               # pre_payout_X / post_payout_X: marcadores, no mueven saldo
        return None, None
    if desc in ("payout", "reserve_for_payout"):
        return "retiros", None
    if desc == "reserve_for_debt_payment":
        return "deudas_ml", "Factura de Mercado Libre (cobro automático del saldo)"
    ml = unidad == "Mercado Libre"
    bruto = _num(fila["GROSS_AMOUNT"])
    if desc == "payment" and not ml and bruto != 0:     # bruto 0 = impuesto sobre una venta, va con las ventas
        if _num(fila["NET_DEBIT_AMOUNT"]) > 0:
            sid = fila["SOURCE_ID"]
            # Los cobros de la factura de ML (publicidad, Full, cargos no retenidos en la venta) tienen ids tipo
            # "000gyvb1xq": son los payment_id de /billing/integration/.../payment/details (verificado 26/10/2026).
            if sid.startswith("000") and not sid.isdigit():
                return "deudas_ml", "Factura de Mercado Libre (cobro automático del saldo)"
            if sid not in cache:
                cache[sid] = _descripcion_pago(token, sid)
            texto = cache[sid]
            credito = any(x in texto.lower() for x in ("mercado crédito", "mercado credito"))
            return ("credito" if credito else "pagos_mp"), texto
        return "otros_cobros", None
    if desc in ("asset_management", "cashback"):
        return "otros_cobros", None
    return "ventas_ml", None


def procesar_reporte(texto, cuenta, token):
    """CSV del reporte → (filas cf_liberaciones, filas cf_mp_egresos, filas cf_saldos). Verifica que el saldo cierre."""
    agg = defaultdict(lambda: [0.0, 0.0, 0])
    egresos, saldos, cache = {}, {}, {}
    saldo = None
    for f in csv.DictReader(io.StringIO(texto), delimiter=";"):
        if not f["DATE"]:                               # fila de totales
            continue
        cr, db_, bal = _num(f["NET_CREDIT_AMOUNT"]), _num(f["NET_DEBIT_AMOUNT"]), _num(f["BALANCE_AMOUNT"])
        dia = f["DATE"][:10]                            # el reporte viene en hora de Argentina (GMT-03)
        if saldo is None:                               # primera fila: saldo inicial
            saldo = bal
            continue
        cat, texto_eg = _categoria(f, token, cache)
        if cat is None:
            continue
        if abs(saldo + cr - db_ - bal) > 0.01:
            raise RuntimeError(f"El saldo no cierra en {cuenta} {f['DATE']} {f['DESCRIPTION']}: {saldo}+{cr}-{db_}≠{bal}")
        saldo = bal
        a = agg[(dia, cat)]
        a[0] += cr; a[1] += db_; a[2] += 1
        saldos[dia] = (bal, f["DATE"])
        if cat in ("credito", "pagos_mp", "deudas_ml"):
            mov = f"{f['SOURCE_ID'] or 'sin-id'}|{dia}"           # las reservas de deuda van y vienen: neto por pago y día
            e = egresos.setdefault(mov, {"cuenta": cuenta, "movimiento": mov, "fecha": dia, "categoria": cat,
                                         "monto": 0.0, "descripcion": texto_eg})
            e["monto"] = round(e["monto"] + db_ - cr, 2)
    ahora = datetime.now(timezone.utc).isoformat()
    lib = [{"fecha": d, "cuenta": cuenta, "categoria": c, "credito": round(v[0], 2), "debito": round(v[1], 2),
            "cantidad": v[2], "actualizado_at": ahora} for (d, c), v in agg.items()]
    sal = [{"fecha": d, "cuenta": cuenta, "saldo": round(b, 2), "fuente": "mp_reporte", "hora": h,
            "creado_por": None, "actualizado_at": ahora} for d, (b, h) in saldos.items()]
    egr = [dict(e, actualizado_at=ahora) for e in egresos.values() if abs(e["monto"]) >= 0.5]
    return lib, egr, sal


def sync_reportes(config, key, desde, hasta):
    cfg = config["channels"][key]
    cuenta = CUENTAS[key]
    token = core.ml_ensure_token(key, cfg)
    # Tramos de a un mes como máximo (la cuenta de Pret mueve ~1.500 filas por día).
    tramos, d = [], desde
    while d <= hasta:
        fin = min(hasta, (d.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1))
        tramos.append((d, fin)); d = fin + timedelta(days=1)
    errores = []
    for a, b in tramos:
        try:
            texto = _pedir_reporte(token, a, b)
        except RuntimeError as e:                       # un mes que falla no frena los siguientes; se reintenta mañana
            errores.append(str(e))
            core.tnlog(f"  ⚠ {cfg['label']} {a}→{b}: {e}")
            continue
        lib, egr, sal = procesar_reporte(texto, cuenta, token)
        # Antes de borrar el tramo: el reporte tiene que arrancar el primer día y no pasarse del último.
        lineas = texto.splitlines()
        if len(lineas) < 2 or lineas[1][:10] != a.isoformat() or any(f["fecha"] > b.isoformat() for f in lib):
            errores.append(f"El reporte {a}→{b} no cubre ese período")
            core.tnlog(f"  ⚠ {cfg['label']} {a}→{b}: el reporte descargado no cubre el período, no se toca nada")
            continue
        # Se reemplaza el tramo entero: si cambia la clasificación, no quedan filas viejas de otra categoría.
        rango = {"cuenta": f"eq.{cuenta}", "and": f"(fecha.gte.{a},fecha.lte.{b})"}
        db.delete(config, "cf_liberaciones", rango)
        db.delete(config, "cf_mp_egresos", rango)
        db.upsert(config, "cf_liberaciones", lib, "fecha,cuenta,categoria")
        db.upsert(config, "cf_mp_egresos", egr, "cuenta,movimiento")
        db.upsert(config, "cf_saldos", sal, "fecha,cuenta")
        ultimo = max(sal, key=lambda s: s["fecha"]) if sal else None
        core.tnlog(f"  ✓ {cfg['label']} {a}→{b}: {len(lib)} filas, {len(egr)} egresos"
                   + (f", saldo {ultimo['fecha']} ${ultimo['saldo']:,.2f}" if ultimo else ""))
    if errores:
        raise RuntimeError("; ".join(errores))


# ── Liberaciones pendientes ────────────────────────────────────────────────

def sync_pendientes(config, key):
    cfg = config["channels"][key]
    cuenta = CUENTAS[key]
    token = core.ml_ensure_token(key, cfg)
    suma = defaultdict(lambda: [0.0, 0])
    vistos = set()
    inicio = datetime.now(AR)
    # De a 7 días para no pasar el límite de offset de la búsqueda.
    for semana in range(0, 70, 7):
        a, b = inicio + timedelta(days=semana), inicio + timedelta(days=semana + 7)
        offset = 0
        while True:
            data = core.ml_get(f"{MP}/v1/payments/search", token, params={
                "range": "money_release_date", "begin_date": a.isoformat(timespec="milliseconds"),
                "end_date": b.isoformat(timespec="milliseconds"), "status": "approved",
                "sort": "money_release_date", "criteria": "asc", "limit": 100, "offset": offset})
            if data is None:
                raise RuntimeError(f"Falló la búsqueda de pagos pendientes de {cfg['label']}")
            for p in data.get("results", []):
                if p["id"] in vistos or p.get("money_release_status") == "released":
                    continue
                vistos.add(p["id"])
                dia = datetime.fromisoformat(p["money_release_date"]).astimezone(AR).date().isoformat()
                s = suma[dia]
                s[0] += (p.get("transaction_details") or {}).get("net_received_amount") or 0
                s[1] += 1
            offset += 100
            if offset >= data["paging"]["total"] or not data.get("results"):
                break
            if offset >= 10000:
                raise RuntimeError(f"Demasiados pagos pendientes en una semana para {cfg['label']}")
            time.sleep(0.2)
    ahora = datetime.now(timezone.utc).isoformat()
    filas = [{"fecha": d, "cuenta": cuenta, "monto": round(v[0], 2), "cantidad": v[1], "actualizado_at": ahora}
             for d, v in sorted(suma.items())]
    db.delete(config, "cf_liberaciones_pendientes", {"cuenta": f"eq.{cuenta}"})
    db.insert(config, "cf_liberaciones_pendientes", filas)
    core.tnlog(f"  ✓ {cfg['label']}: ${sum(f['monto'] for f in filas):,.2f} pendientes de liberar en {len(vistos)} pagos")


# ── Tienda Nube por medio de pago ──────────────────────────────────────────

def sync_tn_medios(config, key, desde):
    cfg = config["channels"][key]
    base = f"https://api.tiendanube.com/v1/{cfg['store_id']}"
    suma = defaultdict(lambda: [0.0, 0])
    page = 1
    while True:
        for intento in range(3):
            r = requests.get(f"{base}/orders", headers=core.tn_headers(cfg), timeout=60, params={
                "per_page": 200, "page": page, "created_at_min": (desde - timedelta(days=7)).isoformat(),
                "fields": "id,status,payment_status,paid_at,gateway,total"})
            if r.status_code == 200 or r.status_code == 404:
                break
            time.sleep(5)
        if r.status_code == 404:                        # TN devuelve 404 cuando la página ya no tiene órdenes
            break
        r.raise_for_status()
        lote = r.json()
        for o in lote:
            if o.get("status") == "cancelled" or o.get("payment_status") not in ("paid", "authorized") or not o.get("paid_at"):
                continue
            dia = datetime.fromisoformat(o["paid_at"].replace("+0000", "+00:00")).astimezone(AR).date()
            if dia < desde:
                continue
            s = suma[(dia.isoformat(), o.get("gateway") or "otro")]
            s[0] += _num(o.get("total")); s[1] += 1
        if len(lote) < 200:
            break
        page += 1
        time.sleep(0.5)
    ahora = datetime.now(timezone.utc).isoformat()
    filas = [{"fecha": d, "tienda": TIENDAS[key], "medio": m, "total": round(v[0], 2), "cantidad": v[1],
              "actualizado_at": ahora} for (d, m), v in suma.items()]
    db.upsert(config, "cf_tn_medios", filas, "fecha,tienda,medio")
    core.tnlog(f"  ✓ {cfg['label']}: {sum(v[1] for v in suma.values())} órdenes pagas desde {desde}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", help="YYYY-MM-DD: carga histórica desde ese día (por defecto, los últimos 3 días)")
    args = ap.parse_args()
    config = db.load_config()
    hasta = _hoy()
    desde = date.fromisoformat(args.desde) if args.desde else hasta - timedelta(days=3)
    errores = []
    for key in CUENTAS:
        if not config["channels"].get(key, {}).get("enabled", True):
            continue
        for paso in (lambda: sync_reportes(config, key, desde, hasta), lambda: sync_pendientes(config, key)):
            try:
                paso()
            except Exception as e:
                errores.append(f"{key}: {e}")
                core.tnlog(f"  ⚠ {key}: {e}")
    for key in TIENDAS:
        try:
            sync_tn_medios(config, key, desde)
        except Exception as e:
            errores.append(f"{key}: {e}")
            core.tnlog(f"  ⚠ {key}: {e}")
    if errores:
        raise SystemExit("Terminó con errores:\n" + "\n".join(errores))


if __name__ == "__main__":
    main()
