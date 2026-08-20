#!/usr/bin/env python3
"""
reposicion/jobs/weekly_calc.py — corre todos los días (GitHub Actions cron;
el nombre del archivo quedó de cuando corría 1 vez por semana). Cada corrida
pisa la fila de repo_calculo_semanal de la semana ISO actual (on_conflict
semana_iso+sku), así que correrlo varias veces en la misma semana no genera
duplicados — sólo refresca los números.

Reusa core.fetch_all() + core.build_rows() (misma lógica de
generar_reporte.py: velocidad, quiebre, confianza, tendencia, sobrestock,
proveedor) y escribe el resultado en repo_calculo_semanal + repo_productos,
en vez de un Excel.

Uso local: python3 -m reposicion.jobs.weekly_calc
"""

import sys
import threading
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reposicion import core, db

DEFAULT_DAYS = 60


def _fetch_catalog(channels):
    """Igual que core.fetch_all() pero solo trae catálogo (productos TN /
    items ML — precio, costo, stock, nombre), no órdenes. Las ventas se arman
    aparte desde repo_ventas_items (ver fetch_all_hybrid) en vez de re-pedir
    60 días de órdenes en vivo en cada corrida — build_rows() no cambia,
    solo cambia de dónde sale results[canal]["sales"/"orders"]."""
    results = {}

    def worker_tn(key, cfg):
        try:
            products = core.tn_get_products(cfg)
            results[key] = {"products": products}
            core.tnlog(f"✓ {cfg['label']}: catálogo — {len(products)} productos")
        except Exception as e:
            core.tnlog(f"✗ {cfg['label']}: ERROR catálogo — {e}")
            results[key] = {"products": []}

    def worker_ml(key, cfg):
        try:
            item_details = core.ml_get_all_items(key, cfg)
            results[key] = {"item_details": item_details}
            core.tnlog(f"✓ {cfg['label']}: catálogo — {len(item_details)} items")
        except Exception as e:
            core.tnlog(f"✗ {cfg['label']}: ERROR catálogo — {e}")
            results[key] = {"item_details": {}}

    threads = []
    for key, cfg in channels.items():
        if not cfg.get("enabled", True):
            continue
        fn = worker_tn if cfg["type"] == "tiendanube" else worker_ml
        t = threading.Thread(target=fn, args=(key, cfg), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    return results


def _fake_tn_orders_from_rows(rows):
    """Reconstruye la misma forma que espera core.parse_tn_sales() a partir
    de filas ya persistidas en repo_ventas_items — parse_tn_sales no se toca."""
    by_order = defaultdict(list)
    for r in rows:
        by_order[r["order_id"]].append(r)
    return [
        {
            "created_at": items[0]["creado_at"],
            "products": [{"sku": r["sku"], "quantity": r["cantidad"]} for r in items],
        }
        for items in by_order.values()
    ]


def _fake_ml_orders_from_rows(rows):
    """Reconstruye la misma forma que esperan core.parse_ml_sales() Y
    core.ml_sales_full_split() (ambas leen order_items[].item.{id,seller_sku})
    — ninguna de las dos se toca."""
    by_order = defaultdict(list)
    for r in rows:
        by_order[r["order_id"]].append(r)
    return [
        {
            "date_created": items[0]["creado_at"],
            "order_items": [
                {"item": {"id": r["item_id"], "seller_sku": r["sku"]}, "quantity": r["cantidad"]}
                for r in items
            ],
        }
        for items in by_order.values()
    ]


def _ventas_rows(config, canal, date_from_str):
    return db.select(config, "repo_ventas_items", params={
        "canal": f"eq.{canal}",
        "fecha": f"gte.{date_from_str}",
        "estado": "eq.activa",
    })


def _resolver_items_cerrados(config, canal, cfg, rows, item_details):
    """Ventas de ML pueden referenciar publicaciones que ya no están activas
    hoy (pausadas/cerradas) y por lo tanto no aparecen en item_details (que
    solo trae status=active — ver core.ml_scroll_item_ids). Sin resolverlas,
    ml_sales_full_split() las clasifica por default como no-Full, inflando
    'Vendido depósito' con ventas que en realidad salieron por Full (caso
    real verificado: SKU PRET210-CBOGRIS, 313 de 815 ventas mal atribuidas).
    Se resuelven una sola vez contra repo_items_ml_cache — una publicación
    cerrada no vuelve a cambiar de logistic_type — y las nuevas se cachean
    para no volver a pedirlas la próxima corrida."""
    faltantes = {r["item_id"] for r in rows if r.get("item_id")} - set(item_details.keys())
    if not faltantes:
        return {}

    cache_rows = db.select(config, "repo_items_ml_cache", params={
        "item_id": f"in.({','.join(faltantes)})",
    })
    cache_by_id = {r["item_id"]: r for r in cache_rows}
    aun_faltantes = [iid for iid in faltantes if iid not in cache_by_id]

    resueltos = {}
    nuevos_encontrados = 0
    if aun_faltantes:
        token = core.ml_ensure_token(canal, cfg)
        nuevos = core.ml_items_by_ids(token, aun_faltantes)
        nuevas_filas = []
        for item_id, body in nuevos.items():
            resueltos[item_id] = body
            nuevas_filas.append({
                "item_id": item_id,
                "canal": canal,
                "sku": core.ml_item_sku(body),
                "logistic_type": (body.get("shipping") or {}).get("logistic_type", ""),
            })
        if nuevas_filas:
            db.upsert(config, "repo_items_ml_cache", nuevas_filas, on_conflict="item_id")
        nuevos_encontrados = len(nuevos)

    for item_id, cached in cache_by_id.items():
        resueltos[item_id] = {"shipping": {"logistic_type": cached.get("logistic_type") or ""}}

    core.tnlog(f"  {canal}: {len(faltantes)} publicaciones de ventas fuera del catálogo activo — "
               f"{len(cache_by_id)} desde caché, {nuevos_encontrados} resueltas ahora "
               f"({len(aun_faltantes) - nuevos_encontrados} no encontradas)")
    return resueltos


def fetch_all_hybrid(config):
    """Catálogo en vivo (precio/costo/stock/nombre — cambia todos los días,
    no tiene sentido persistirlo con esta cadencia) + ventas desde
    repo_ventas_items (sync incremental diario vía reposicion/ventas_sync.py,
    wireado en reposicion/jobs/daily_stock.py) en vez de volver a pedir 60
    días de órdenes en vivo — el cuello de botella real (~22000 órdenes de
    ML Pret cada corrida)."""
    channels = config["channels"]
    results = _fetch_catalog(channels)

    for canal in ("tn_pret", "tn_lavan"):
        if canal not in results:
            continue
        rows = _ventas_rows(config, canal, core.DATE_FROM_STR)
        fake_orders = _fake_tn_orders_from_rows(rows)
        sales_total, sales_first, sales_second = core.parse_tn_sales(fake_orders)
        results[canal]["sales"] = sales_total
        results[canal]["sales_first"] = sales_first
        results[canal]["sales_second"] = sales_second
        core.tnlog(f"  {canal}: {len(rows)} líneas de venta desde repo_ventas_items")

    for canal in ("ml_pret", "ml_lavan"):
        if canal not in results:
            continue
        rows = _ventas_rows(config, canal, core.DATE_FROM_STR)
        resueltos = _resolver_items_cerrados(config, canal, channels[canal], rows, results[canal]["item_details"])
        results[canal]["item_details"].update(resueltos)
        fake_orders = _fake_ml_orders_from_rows(rows)
        sales_total, sales_first, sales_second = core.parse_ml_sales(fake_orders)
        results[canal]["orders"] = fake_orders  # ml_sales_full_split() lo usa dentro de build_rows()
        results[canal]["sales"] = sales_total
        results[canal]["sales_first"] = sales_first
        results[canal]["sales_second"] = sales_second
        core.tnlog(f"  {canal}: {len(rows)} líneas de venta desde repo_ventas_items")

    return results


def semana_iso(d=None):
    d = d or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def get_coverage_days(config):
    rows = db.select(config, "repo_settings", params={"clave": "eq.coverage_days", "select": "valor"})
    if rows:
        return int(rows[0]["valor"])
    return 60  # default de negocio (ver Contexto del plan — reemplaza el 40 hardcodeado original)


def get_descontinuados(config):
    rows = db.select(config, "repo_productos", params={"descontinuado": "eq.true", "select": "sku"})
    return {r["sku"] for r in rows}


def _parse_fecha_ddmmyyyy(s):
    if not s or s == "—":
        return None
    try:
        return datetime.strptime(s, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def _int_or_none(v):
    return v if isinstance(v, int) else None


def row_to_calculo(row, semana, coverage_days):
    sobrestock = core.sobrestock_category(row)
    return {
        "semana_iso":             semana,
        "fecha_corrida":          datetime.now(timezone.utc).isoformat(),
        "sku":                    row["SKU"],
        "stock_deposito":         row["Stock actual"] if isinstance(row["Stock actual"], int) else None,
        "vel_diaria_deposito":    row.get("Vel. diaria depósito"),
        "vel_semanal_deposito":   row.get("Vel. semanal depósito"),
        "dias_activo":            row.get("Días activo (período)"),
        "confianza":              row.get("Confianza métrica"),
        "tendencia_pct":          row.get("Tendencia (%)"),
        "dias_quiebre_deposito":  _int_or_none(row.get("Días para quiebre")),
        "fecha_quiebre_deposito": _parse_fecha_ddmmyyyy(row.get("Fecha quiebre est.")),
        "alerta_deposito":        row.get("Alerta stock"),
        "sobrestock_categoria":   sobrestock[0] if sobrestock else None,
        "sobrestock_accion":      sobrestock[1] if sobrestock else None,
        "a_reponer_deposito":     _int_or_none(row.get("A reponer (uds)")),
        "vendido_deposito":       row.get("Unid. depósito"),
        "revenue_60d":            row.get("Revenue 60d ($)"),
        "ganancia_60d":           row.get("Ganancia bruta 60d ($)"),
        "canal_dominante":        row.get("Canal dominante"),
        "unid_ml_pret":           row.get("Unid. ML Pret"),
        "unid_ml_lavan":          row.get("Unid. ML Lavan"),
        "unid_tn_pret":           row.get("Unid. TN Pret"),
        "unid_tn_lavan":          row.get("Unid. TN Lavan"),
        # Full Pret y Full Lavan son pools separados (ver core.py:full_metrics_marca)
        # — nunca combinar stock/velocidad/quiebre entre marcas.
        "vendido_full_pret":      row.get("Unid. Full Pret"),
        "stock_full_pret":         row.get("Stock Full Pret", 0),
        "vel_diaria_full_pret":    row.get("Vel. Full Pret (diaria)"),
        "vel_semanal_full_pret":   row.get("Vel. Full Pret (semanal)"),
        "dias_quiebre_full_pret":  _int_or_none(row.get("Días quiebre Full Pret")),
        "fecha_quiebre_full_pret": _parse_fecha_ddmmyyyy(row.get("Fecha quiebre Full Pret")),
        "alerta_full_pret":        row.get("Alerta Full Pret"),
        "a_reponer_full_pret":     _int_or_none(row.get("A enviar Full Pret (uds)")),
        "vendido_full_lavan":      row.get("Unid. Full Lavan"),
        "stock_full_lavan":         row.get("Stock Full Lavan", 0),
        "vel_diaria_full_lavan":    row.get("Vel. Full Lavan (diaria)"),
        "vel_semanal_full_lavan":   row.get("Vel. Full Lavan (semanal)"),
        "dias_quiebre_full_lavan":  _int_or_none(row.get("Días quiebre Full Lavan")),
        "fecha_quiebre_full_lavan": _parse_fecha_ddmmyyyy(row.get("Fecha quiebre Full Lavan")),
        "alerta_full_lavan":        row.get("Alerta Full Lavan"),
        "a_reponer_full_lavan":     _int_or_none(row.get("A enviar Full Lavan (uds)")),
        "coverage_days_usado":    coverage_days,
    }


def _dedupe_by_sku(rows):
    """El catálogo real de Tiendanube tiene SKUs repetidos entre productos
    distintos (error de carga de datos, no de este script) — Postgres/PostgREST
    no permite que un mismo upsert toque la misma fila (mismo SKU) dos veces
    en un solo comando. Nos quedamos con la variante de mayor venta total por
    SKU duplicado y avisamos cuáles fueron, para que se pueda corregir en TN."""
    por_sku = {}
    duplicados = set()
    for r in rows:
        sku = r["SKU"]
        actual = por_sku.get(sku)
        if actual is None:
            por_sku[sku] = r
        else:
            duplicados.add(sku)
            if r.get("Total vendido", 0) > actual.get("Total vendido", 0):
                por_sku[sku] = r
    if duplicados:
        core.tnlog(f"  ⚠ {len(duplicados)} SKUs duplicados en el catálogo (se usó la variante con más ventas): "
                   f"{', '.join(sorted(duplicados)[:20])}{' ...' if len(duplicados) > 20 else ''}")
    return list(por_sku.values())


def main():
    config = db.load_config()
    coverage_days = get_coverage_days(config)
    descontinuados = get_descontinuados(config)
    semana = semana_iso()

    core.tnlog(f"═══ Cálculo semanal {semana}  |  cobertura target: {coverage_days}d ═══")
    core.configure(days=DEFAULT_DAYS, coverage_days=coverage_days)
    core.load_supplier_map()

    results = fetch_all_hybrid(config)

    rows = core.build_rows(results)
    core.tnlog(f"  {len(rows)} variantes procesadas")

    rows = [r for r in rows if r["SKU"] not in descontinuados]
    core.tnlog(f"  {len(rows)} tras excluir descontinuados")

    rows = _dedupe_by_sku(rows)
    core.tnlog(f"  {len(rows)} tras deduplicar por SKU")

    productos = [
        {
            "sku": r["SKU"],
            "nombre": r["Producto"],
            "proveedor_auto": r["Proveedor"],
            "categoria_auto": core.classify_product(r["Producto"]),
        }
        for r in rows
    ]
    db.upsert(config, "repo_productos", productos, on_conflict="sku")

    calculos = [row_to_calculo(r, semana, coverage_days) for r in rows]
    db.upsert(config, "repo_calculo_semanal", calculos, on_conflict="semana_iso,sku")

    core.tnlog(f"✓ {len(calculos)} filas escritas en repo_calculo_semanal ({semana})")


if __name__ == "__main__":
    main()
