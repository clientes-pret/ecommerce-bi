#!/usr/bin/env python3
"""
reposicion/rev_contenido_sync.py — contenido para publicar y costo por SKU, para el portal de revendedores.

Lee SOLO LECTURA los productos de Tiendanube (Pret primero; Casa Lavan para los SKU que solo existen ahí) y guarda
por SKU en `rev_catalogo_info`: título (nombre + variante), descripción en texto plano, fotos (URLs públicas),
costo y precio de lista. El costo es la base del precio mayorista (costo + margen %); nunca llega al front.

Uso (manual; no es cron todavía):
    python3 -m reposicion.rev_contenido_sync            # prueba: muestra resumen, no escribe
    python3 -m reposicion.rev_contenido_sync --aplicar  # escribe en Supabase
"""
import html
import re
import sys
from datetime import datetime, timezone

from reposicion import core, db


def _es(v):
    """Tiendanube devuelve textos como {'es': '...', 'pt': ...} o string."""
    if isinstance(v, dict):
        return (v.get("es") or next(iter(v.values()), "") or "").strip()
    return (v or "").strip() if isinstance(v, str) else ""


def _texto(h):
    """HTML de la descripción → texto plano con saltos de línea (Mercado Libre no acepta HTML)."""
    if not h:
        return ""
    h = re.sub(r"(?i)<\s*br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", h)
    h = re.sub(r"(?i)<\s*li[^>]*>", "• ", h)
    h = re.sub(r"<[^>]+>", "", h)
    h = html.unescape(h).replace("\xa0", " ")
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n\n", h)
    return h.strip()


def _num(v):
    try:
        n = float(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def filas_desde(products, fuente):
    filas = {}
    for p in products:
        nombre = _es(p.get("name"))
        desc = _texto(_es(p.get("description")))
        imgs = sorted(p.get("images") or [], key=lambda i: i.get("position") or 0)
        por_id = {i.get("id"): i.get("src") for i in imgs}
        todas = [i.get("src") for i in imgs if i.get("src")]
        for v in p.get("variants") or []:
            sku = (v.get("sku") or "").strip()
            if not sku or sku in filas:
                continue
            valores = [_es(x) for x in (v.get("values") or []) if _es(x)]
            titulo = " - ".join([nombre] + valores) if valores else nombre
            foto_var = por_id.get(v.get("image_id"))
            fotos = ([foto_var] if foto_var else []) + [f for f in todas if f != foto_var]
            filas[sku] = {
                "sku": sku, "titulo": titulo, "descripcion": desc, "fotos": fotos[:12],
                "costo": _num(v.get("cost")), "precio_sugerido": _num(v.get("price")),
                "fuente": fuente, "actualizado_at": datetime.now(timezone.utc).isoformat(),
            }
    return filas


def main():
    aplicar = "--aplicar" in sys.argv
    config = db.load_config()
    todas = {}
    for canal in ("tn_pret", "tn_lavan"):  # Pret primero: gana si el SKU está en las dos
        cfg = config["channels"][canal]
        productos = core.tn_get_products(cfg)
        nuevas = filas_desde(productos, canal)
        agregadas = 0
        for sku, f in nuevas.items():
            if sku not in todas:
                todas[sku] = f
                agregadas += 1
        print(f"{canal}: {len(productos)} productos, {len(nuevas)} SKU, {agregadas} nuevos")
    filas = list(todas.values())
    print(f"Total {len(filas)} SKU · con costo {sum(1 for f in filas if f['costo'])} · con fotos {sum(1 for f in filas if f['fotos'])} "
          f"· con descripción {sum(1 for f in filas if f['descripcion'])}")
    if not aplicar:
        print("Modo prueba: no se escribió nada (usar --aplicar).")
        return
    db.upsert(config, "rev_catalogo_info", filas, on_conflict="sku", chunk=200)
    print("Guardado en rev_catalogo_info.")


if __name__ == "__main__":
    main()
