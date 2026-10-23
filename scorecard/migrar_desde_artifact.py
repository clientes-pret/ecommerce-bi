"""Pasa la historia del Scorecard (base del artifact) a Supabase.

Lee scorecard/data/artifact_days.json (export de la colección "days" del artifact) y la carga en
scorecard_canal_dia, scorecard_ads_dia y scorecard_notas. Los "pending" quedan en NULL.

Uso (desde la carpeta ecommerce-bi):
    venv/bin/python scorecard/migrar_desde_artifact.py            # simulación: muestra qué cargaría
    venv/bin/python scorecard/migrar_desde_artifact.py --aplicar  # carga (upsert, se puede repetir)
"""
import json
import os
import sys
from collections import defaultdict

AQUI = os.path.dirname(os.path.abspath(__file__))
EXPORT = os.path.join(AQUI, "data", "artifact_days.json")

CANALES = {  # clave del artifact -> (marca, canal)
    "tn_pret_home": ("pret", "tn"),
    "ml_pret_home": ("pret", "ml"),
    "tn_casa_lavan": ("lavan", "tn"),
    "ml_casa_lavan": ("lavan", "ml"),
}
ADS = {
    "meta_ads_pret_home_ars": ("pret", "meta"),
    "meta_ads_casa_lavan_ars": ("lavan", "meta"),
    "google_ads_pret_home_ars": ("pret", "google"),
    "google_ads_casa_lavan_ars": ("lavan", "google"),
    "ml_ads_pret": ("pret", "ml"),
    "ml_ads_lavan": ("lavan", "ml"),
}
CAMPOS = ["ventas", "ordenes", "facturacion_neta", "costo_productos", "costo_envio",
          "local_efectivo", "local_otros_medios", "local_gastado"]


def num(v):
    """Número real o None ("pending", textos y vacíos = sin dato)."""
    if isinstance(v, bool):
        return None
    return v if isinstance(v, (int, float)) else None


def filas_de(dia):
    fecha = dia["date"]
    canal_rows, ads_rows = [], []
    for clave, (marca, canal) in CANALES.items():
        venta = dia.get(clave)
        r = dict.fromkeys(CAMPOS)
        if isinstance(venta, dict):
            r["ventas"] = num(venta.get("revenue"))
            o = num(venta.get("orders"))
            r["ordenes"] = int(o) if o is not None else None
        r["facturacion_neta"] = num(dia.get(f"facturacion_neta_{clave}"))
        r["costo_productos"] = num(dia.get(f"costo_productos_{clave}"))
        r["costo_envio"] = num(dia.get(f"costo_envio_{clave}"))
        if any(v is not None for v in r.values()):
            canal_rows.append({"fecha": fecha, "marca": marca, "canal": canal, "fuente": "artifact", **r})
    loc = dia.get("local_fisico_pret")
    if isinstance(loc, dict):
        r = dict.fromkeys(CAMPOS)
        r["ventas"] = num(loc.get("total_recibido", loc.get("revenue")))
        r["local_efectivo"] = num(loc.get("efectivo"))
        r["local_otros_medios"] = num(loc.get("otros_medios"))
        r["local_gastado"] = num(loc.get("gastado"))
        if any(v is not None for v in r.values()):
            canal_rows.append({"fecha": fecha, "marca": "pret", "canal": "local", "fuente": "artifact", **r})
    for clave, (marca, plat) in ADS.items():
        v = num(dia.get(clave))
        if v is not None:
            ads_rows.append({"fecha": fecha, "marca": marca, "plataforma": plat, "inversion": v, "fuente": "artifact"})
    nota = dia.get("error")
    nota_row = [{"fecha": fecha, "nota": nota}] if isinstance(nota, str) and nota.strip() else []
    return canal_rows, ads_rows, nota_row


def lotes(xs, n=200):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def main():
    dias = json.load(open(EXPORT, encoding="utf-8"))
    canal, ads, notas = [], [], []
    for _, dia in sorted(dias.items()):
        c, a, n = filas_de(dia)
        canal += c; ads += a; notas += n

    print(f"Días en el export: {len(dias)}  ({min(dias)} a {max(dias)})")
    print(f"Filas a cargar: {len(canal)} canal-día · {len(ads)} ads-día · {len(notas)} notas")
    por_mes = defaultdict(float)
    for r in canal:
        if r["ventas"] is not None:
            por_mes[r["fecha"][:7]] += r["ventas"]
    print("Ventas por mes (control):")
    for m, v in sorted(por_mes.items()):
        print(f"  {m}  $ {v:,.0f}".replace(",", "."))

    if "--aplicar" not in sys.argv:
        print("\nSimulación: no se escribió nada. Corré con --aplicar para cargar.")
        return

    sys.path.insert(0, os.path.join(AQUI, "..", "proveedores", "scripts"))
    import _sb as sb
    for lote in lotes(canal):
        sb.upsert("scorecard_canal_dia", lote, "fecha,marca,canal")
    for lote in lotes(ads):
        sb.upsert("scorecard_ads_dia", lote, "fecha,marca,plataforma")
    if notas:
        sb.upsert("scorecard_notas", notas, "fecha")
    n = sb.select("scorecard_canal_dia", "select=fecha&limit=10000")
    print(f"\n✅ Cargado. scorecard_canal_dia tiene {len(n or [])} filas.")


if __name__ == "__main__":
    main()
