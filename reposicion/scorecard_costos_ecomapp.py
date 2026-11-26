#!/usr/bin/env python3
"""
reposicion/scorecard_costos_ecomapp.py — Carga la lista de costos de Ecomm-App que usa el Scorecard de Mercado Libre.

Lee el archivo "Actualización de productos" de Ecomm-App (.xlsx o .csv), con las columnas `sku` y `precio_compra`
(costo con IVA), y actualiza la tabla scorecard_costos_ecomapp. Solo agrega o pisa: un SKU que no venga en el archivo
conserva el costo que tenía. Los renglones sin costo o con costo 0 se ignoran.

Hay que volver a correrlo cuando cambian los costos en Ecomm-App o se dan de alta productos.

Uso: python3 -m reposicion.scorecard_costos_ecomapp archivo.xlsx             (simula, no guarda)
     python3 -m reposicion.scorecard_costos_ecomapp archivo.xlsx --aplicar   (guarda)
Pruebas: python3 -m reposicion.tests.test_scorecard_ml
"""

import argparse
import csv
import re
from datetime import datetime, timezone

from reposicion import db

TABLA = "scorecard_costos_ecomapp"


def numero(v):
    """Número de una celda: acepta 10154, "10.154,00", "10.154" y "10154.50". None si no es un número."""
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v or "").replace("$", "").replace(" ", "").strip()
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def leer_costos(filas):
    """De las filas del archivo (la primera es el encabezado) devuelve ({sku: costo}, SKUs repetidos con costos
    distintos; queda el mayor)."""
    filas = iter(filas)
    encabezado = [str(c or "").strip().lower() for c in next(filas, [])]
    if "sku" not in encabezado or "precio_compra" not in encabezado:
        raise ValueError("El archivo no tiene las columnas 'sku' y 'precio_compra' en la primera fila")
    i_sku, i_costo = encabezado.index("sku"), encabezado.index("precio_compra")
    costos, repetidos = {}, set()
    for f in filas:
        if len(f) <= max(i_sku, i_costo):
            continue
        sku, costo = str(f[i_sku] or "").strip(), numero(f[i_costo])
        if not sku or costo is None or costo <= 0:
            continue
        if sku in costos and abs(costos[sku] - costo) > 0.005:
            repetidos.add(sku)
        costos[sku] = max(round(costo, 2), costos.get(sku, 0))
    return costos, sorted(repetidos)


def filas_de(ruta):
    if ruta.lower().endswith(".csv"):
        with open(ruta, newline="", encoding="utf-8-sig") as f:
            return list(csv.reader(f))
    import openpyxl  # solo hace falta para .xlsx (carga manual)

    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    hoja = wb["Actualización de productos"] if "Actualización de productos" in wb.sheetnames else wb.worksheets[0]
    return [list(f) for f in hoja.iter_rows(values_only=True)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archivo")
    ap.add_argument("--aplicar", action="store_true", help="guardar en Supabase (sin esto solo muestra)")
    args = ap.parse_args()
    costos, repetidos = leer_costos(filas_de(args.archivo))
    if not costos:
        raise SystemExit("No se encontró ningún SKU con costo en el archivo")
    config = db.load_config()
    antes = {f["sku"]: float(f["costo"]) for f in db.select(config, TABLA, {"select": "sku,costo", "order": "sku"})}
    nuevos = sum(1 for s in costos if s not in antes)
    cambiados = sum(1 for s, c in costos.items() if s in antes and abs(antes[s] - c) > 0.005)
    print(f"{len(costos)} SKUs con costo en el archivo · {nuevos} nuevos · {cambiados} cambian de costo")
    if repetidos:
        print(f"⚠ {len(repetidos)} SKUs repetidos con costos distintos (queda el mayor): {', '.join(repetidos[:10])}")
    if not args.aplicar:
        print("Simulación: no se guardó nada (agregar --aplicar)")
        return
    ahora = datetime.now(timezone.utc).isoformat()
    db.upsert(config, TABLA, [{"sku": s, "costo": c, "actualizado_en": ahora} for s, c in sorted(costos.items())], "sku")
    print("✓ Guardado")


if __name__ == "__main__":
    main()
