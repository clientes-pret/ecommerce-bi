#!/usr/bin/env python3
"""
reposicion/jobs/weekly_calc.py — corre 1 vez por semana (GitHub Actions cron).

Reusa core.fetch_all() + core.build_rows() (misma lógica de
generar_reporte.py: velocidad, quiebre, confianza, tendencia, sobrestock,
proveedor) y escribe el resultado en repo_calculo_semanal + repo_productos,
en vez de un Excel.

Uso local: python3 -m reposicion.jobs.weekly_calc
"""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reposicion import core, db

DEFAULT_DAYS = 60


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
        # Full Pret y Full Lavan son pools separados (ver core.py:full_metrics_marca)
        # — nunca combinar stock/velocidad/quiebre entre marcas.
        "stock_full_pret":         row.get("Stock Full Pret", 0),
        "vel_diaria_full_pret":    row.get("Vel. Full Pret (diaria)"),
        "vel_semanal_full_pret":   row.get("Vel. Full Pret (semanal)"),
        "dias_quiebre_full_pret":  _int_or_none(row.get("Días quiebre Full Pret")),
        "fecha_quiebre_full_pret": _parse_fecha_ddmmyyyy(row.get("Fecha quiebre Full Pret")),
        "alerta_full_pret":        row.get("Alerta Full Pret"),
        "a_reponer_full_pret":     _int_or_none(row.get("A enviar Full Pret (uds)")),
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

    results, errors = core.fetch_all(config["channels"])
    if errors:
        core.tnlog(f"⚠ Errores en canales: {errors}")

    rows = core.build_rows(results)
    core.tnlog(f"  {len(rows)} variantes procesadas")

    rows = [r for r in rows if r["SKU"] not in descontinuados]
    core.tnlog(f"  {len(rows)} tras excluir descontinuados")

    rows = _dedupe_by_sku(rows)
    core.tnlog(f"  {len(rows)} tras deduplicar por SKU")

    productos = [
        {"sku": r["SKU"], "nombre": r["Producto"], "proveedor_auto": r["Proveedor"]}
        for r in rows
    ]
    db.upsert(config, "repo_productos", productos, on_conflict="sku")

    calculos = [row_to_calculo(r, semana, coverage_days) for r in rows]
    db.upsert(config, "repo_calculo_semanal", calculos, on_conflict="semana_iso,sku")

    core.tnlog(f"✓ {len(calculos)} filas escritas en repo_calculo_semanal ({semana})")


if __name__ == "__main__":
    main()
