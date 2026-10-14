"""Importa la planilla de Jackie (data/movimientos.json) y el catálogo de SKUs (data/productos.json)
al Supabase de reposición.  Uso (desde la carpeta ecommerce-bi):
    venv/bin/python proveedores/scripts/importar.py            # no hace nada si ya se importó
    venv/bin/python proveedores/scripts/importar.py --force    # reemplaza lo importado de la planilla
"""
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import _sb as sb

AQUI = os.path.join(os.path.dirname(__file__), "..", "data")
force = "--force" in sys.argv


def lotes(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def grupo_de(n):
    n = re.sub(r"^(A M|M|A|Z M|Z) ", "", n)
    n = re.sub(r" (P|Z)$", "", n)
    return re.sub(r"^IVA ", "", n).strip()


# ¿Está aplicada la migración?
try:
    sb.select("cc_movimientos", "select=id&limit=1")
except SystemExit:
    raise SystemExit("Primero hay que aplicar la migración:  supabase db push   (desde la carpeta ecommerce-bi)")

movs = json.load(open(os.path.join(AQUI, "movimientos.json"), encoding="utf-8"))
# Hay 1 ajuste de la planilla sin proveedor (20/07/2026, $69.128): va a "SIN ASIGNAR" para que los totales cierren.
for m in movs:
    m["p"] = m["p"] or "SIN ASIGNAR"
ya = sb.select("cc_movimientos", "select=id&origen=eq.planilla&limit=1")
if ya and not force:
    print("Ya hay movimientos importados de la planilla. Usá --force para reemplazarlos.")
    sys.exit(0)
if ya and force:
    sb.delete("cc_movimientos", "origen=eq.planilla")
    print("Borrados los movimientos previos de la planilla.")

corte = (date.today() - timedelta(days=365)).isoformat()
ultima = {}
for m in movs:
    if m["p"] and m["f"] > ultima.get(m["p"], ""):
        ultima[m["p"]] = m["f"]
nombres = sorted(ultima)
sb.upsert("cc_proveedores", [{"nombre": n, "grupo": grupo_de(n), "activo": ultima[n] >= corte} for n in nombres], "nombre", ignore=True)
ids = {p["nombre"]: p["id"] for p in sb.select("cc_proveedores", "select=id,nombre&limit=1000")}

filas = []
for m in movs:
    num = re.search(r"\d{4,5}\s+\d{4,}", m["d"])
    filas.append({
        "proveedor_id": ids[m["p"]], "fecha": m["f"], "tipo": m["t"], "detalle": m["d"], "numero": num.group(0) if num else None,
        "ingreso": m["i"], "pago": m["g"], "medio_pago": m["m"], "origen": "planilla", "orden_original": m["o"],
    })
n = 0
for l in lotes(filas, 500):
    sb.insert("cc_movimientos", l)
    n += len(l)
    print(f"\rMovimientos: {n}/{len(filas)}", end="", flush=True)
print()

ruta = os.path.join(AQUI, "productos.json")
if os.path.exists(ruta):
    prods = json.load(open(ruta, encoding="utf-8"))
    k = 0
    for l in lotes(prods, 500):
        sb.upsert("cc_productos", l, "sku")
        k += len(l)
        print(f"\rProductos: {k}/{len(prods)}", end="", flush=True)
    print()

s = sb.select("cc_saldos", "select=ingresos,pagos,saldo&limit=1000")
ti, tp, ts = (sum(float(x[c]) for x in s) for c in ("ingresos", "pagos", "saldo"))
print(f"Ingresos {ti:,.2f} | Pagos {tp:,.2f} | Saldo {ts:,.2f}")
print("La planilla dice: ingresos 10.090.420.541 | pagos 9.730.800.673 | saldo 359.619.868")
