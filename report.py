#!/usr/bin/env python3
"""
report.py — Motor BI + generador de dashboard HTML v3

Pestañas:
  1. Overview       — KPIs últimos 30d, revenue por canal, pareto
  2. Productos      — tabla unificada por SKU con health score
  3. SKU Detail     — drill-down por SKU con todas las métricas
  4. Sin Ventas     — activos sin ventas, filtro por antigüedad
  5. Quiebre Stock  — stock=0 con rotación + discontinuados
  6. Planificador   — reposición basada en velocidad reciente
  7. BI & Métricas  — análisis avanzado
  8. ML Publicaciones — categorización Muerto/Invisible/Oportunidad/Ganador por publicación

Cambios v3:
  - Navigation drawer (menú lateral deslizable tipo hamburguesa)
  - Nueva pestaña ML Publicaciones con lógica de categorización
  - Filtro por marca (Pret a Home / Casa Lavan) en ML Publicaciones

Uso:
  python3 report.py
  python3 report.py --desde 2025-03-01 --hasta 2025-12-31
"""

import json, argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

ROOT     = Path(__file__).parent
DATA_DIR = ROOT / "data"

def load_config():
    with open(ROOT / "config.json") as f:
        return json.load(f)

def load_cache(canal_key):
    path = DATA_DIR / f"cache_{canal_key}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)

def days_since(date_str):
    if not date_str or date_str == "--":
        return None
    try:
        return (datetime.now() - datetime.strptime(date_str[:10], "%Y-%m-%d")).days
    except:
        return None

# ──────────────────────────────────────────────────────────────────────────────
# NORMALIZACIÓN
# ──────────────────────────────────────────────────────────────────────────────

def normalize_ml_orders(orders, items, canal_key, brand):
    result = []
    for o in orders:
        if not isinstance(o, dict):
            continue
        date = o.get("date_created", "")[:10]
        if not date:
            continue
        for line in o.get("order_items", []):
            iid      = line.get("item", {}).get("id", "")
            var_id   = str(line.get("item", {}).get("variation_id") or "")
            item     = items.get(iid, {})

            var_skus = item.get("var_skus", {})
            if var_id and var_skus.get(var_id):
                sku = var_skus[var_id]
            elif item.get("sku"):
                sku = item["sku"]
            else:
                sku = line.get("item", {}).get("seller_custom_field") or iid

            result.append({
                "date":       date,
                "month":      date[:7],
                "canal":      canal_key,
                "brand":      brand,
                "item_id":    iid,
                "sku":        sku,
                "title":      item.get("title") or line.get("item", {}).get("title", ""),
                "thumbnail":  item.get("thumbnail", ""),
                "permalink":  item.get("permalink", ""),
                "qty":        line.get("quantity", 1),
                "unit_price": line.get("unit_price", 0),
                "revenue":    line.get("quantity", 1) * line.get("unit_price", 0),
                "order_id":   str(o.get("id", "")),
            })
    return result

def normalize_tn_orders(orders, items, canal_key, brand):
    result = []
    for o in orders:
        if not isinstance(o, dict):
            continue
        date = o.get("created_at", "")[:10]
        if not date:
            continue
        for line in o.get("products", []):
            if not isinstance(line, dict):
                continue
            vid   = str(line.get("variant_id") or line.get("product_id") or "")
            item  = items.get(vid, {})
            sku   = item.get("sku") or line.get("sku") or vid
            price = float(line.get("price", 0) or 0)
            qty   = int(line.get("quantity", 1) or 1)
            result.append({
                "date":       date,
                "month":      date[:7],
                "canal":      canal_key,
                "brand":      brand,
                "item_id":    vid,
                "sku":        sku,
                "title":      item.get("title") or line.get("name", ""),
                "thumbnail":  item.get("thumbnail", ""),
                "permalink":  item.get("permalink", ""),
                "qty":        qty,
                "unit_price": price,
                "revenue":    qty * price,
                "order_id":   str(o.get("id", "")),
            })
    return result

# ──────────────────────────────────────────────────────────────────────────────
# ANÁLISIS ML PUBLICACIONES — build_ml_publications
# ──────────────────────────────────────────────────────────────────────────────

def build_ml_publications(ml_caches, all_lines, date_from, date_to):
    """
    Construye análisis a nivel publicación ML con categorización:
    Muerto / Invisible / Oportunidad / Ganador.

    Ventana de análisis: últimos 10 días (más granular y preciso).

    Visitas: se usan SOLO si el fetcher las guarda en visits_10d o visits_30d.
    Si no hay dato real de visitas, la categorización se basa únicamente en ventas
    y se muestra '—' en la columna de visitas (no se estima).

    Categorización:
      - Con visitas reales:
          VISITAS_MUERTO     = 5    → <5 visitas y 0 ventas → Muerto
          MIN_VISITAS_ACTIVO = 15   → mínimo para Oportunidad/Ganador (escala 10d)
          CONVERSION_BAJA    = 0.01 → 1% umbral
      - Sin visitas reales (solo ventas):
          0 ventas → Muerto
          >0 ventas → Ganador (vendió, punto)
    """

    VISITAS_MUERTO     = 5
    MIN_VISITAS_ACTIVO = 15
    CONVERSION_BAJA    = 0.01
    WINDOW_DAYS        = 10  # ventana de análisis

    now         = datetime.now()
    window_from = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    window_to   = now.strftime("%Y-%m-%d")

    # Agrupar líneas ML en la ventana por item_id
    ventas_by_item = defaultdict(lambda: {"qty": 0, "revenue": 0.0, "orders": set()})
    for line in all_lines:
        if not line["canal"].startswith("ml"):
            continue
        if line["date"] < window_from or line["date"] > window_to:
            continue
        iid = line["item_id"]
        ventas_by_item[iid]["qty"]     += line["qty"]
        ventas_by_item[iid]["revenue"] += line["revenue"]
        ventas_by_item[iid]["orders"].add(line["order_id"])

    publications = []

    for canal_key, cache in ml_caches.items():
        if not cache:
            continue
        brand = cache.get("brand", "")
        items = cache.get("items", {})

        for iid, item in items.items():
            title        = item.get("title", "")
            sku          = item.get("sku", "") or iid
            thumbnail    = item.get("thumbnail", "")
            permalink    = item.get("permalink", "")
            status       = item.get("status", "active")
            price        = float(item.get("price", 0) or 0)
            last_updated = item.get("last_updated", "") or item.get("date_last_updated", "") or ""
            # Limpiar a solo fecha si viene con hora
            if last_updated and "T" in last_updated:
                last_updated = last_updated[:10]

            # Visitas reales ÚNICAMENTE — sin estimación
            visitas = None  # None = sin dato
            if "visits_10d" in item:
                visitas = int(item["visits_10d"] or 0)
            elif "visits_30d" in item:
                # Normalizar a ventana de 10 días (proporcional)
                v30 = int(item["visits_30d"] or 0)
                visitas = round(v30 * WINDOW_DAYS / 30)
            elif "visits" in item:
                # Campo genérico — usar tal cual, aclarar en UI
                visitas = int(item["visits"] or 0)
            # Si no hay ningún campo → visitas = None

            # Ventas en la ventana
            v_data  = ventas_by_item.get(iid, {})
            ventas  = v_data.get("qty", 0)
            revenue = v_data.get("revenue", 0.0)
            orders  = len(v_data.get("orders", set()))

            # Conversión — solo si tenemos visitas reales
            conversion = None
            if visitas is not None and visitas > 0:
                conversion = ventas / visitas

            # Categorización
            if visitas is not None:
                # Tenemos datos de visitas → lógica completa
                if visitas < VISITAS_MUERTO and ventas == 0:
                    categoria   = "muerto"
                    diagnostico = f"Sin tráfico ({visitas} visitas) y sin ventas en {WINDOW_DAYS}d."
                    accion      = "Evaluar si vale la pena. Pausar o refactorizar completamente."
                    prioridad   = 3
                elif visitas < MIN_VISITAS_ACTIVO:
                    categoria   = "invisible"
                    diagnostico = f"Poco tráfico ({visitas} visitas en {WINDOW_DAYS}d). Problema: visibilidad."
                    accion      = "Mejorar título, fotos y palabras clave. Revisar precio relativo al mercado."
                    prioridad   = 2
                elif conversion is not None and conversion < CONVERSION_BAJA:
                    categoria   = "oportunidad"
                    diagnostico = f"Buen tráfico ({visitas} visitas) pero conversión baja ({conversion*100:.1f}%). Desperdicia visitas."
                    accion      = "Optimizar descripción, precio, fotos y reputación. Revisar competencia directa."
                    prioridad   = 1
                else:
                    categoria   = "ganador"
                    conv_str    = f"{conversion*100:.1f}%" if conversion is not None else "s/d"
                    diagnostico = f"Buen tráfico y buena conversión ({conv_str}). Publicación rentable."
                    accion      = "No optimizar, escalar. Aumentar stock, publicidad y variantes si aplica."
                    prioridad   = 4
            else:
                # Sin dato de visitas → categorizar solo por ventas
                if ventas == 0:
                    categoria   = "muerto"
                    diagnostico = f"Sin ventas en los últimos {WINDOW_DAYS} días. Sin datos de visitas."
                    accion      = "Evaluar si vale la pena. Considerar pausar."
                    prioridad   = 3
                else:
                    categoria   = "ganador"
                    diagnostico = f"{ventas} ventas en {WINDOW_DAYS}d. Sin datos de visitas para calcular conversión."
                    accion      = "Vendiendo bien. Para análisis completo, agregá visits_10d al fetcher."
                    prioridad   = 4

            publications.append({
                "item_id":      iid,
                "sku":          sku,
                "title":        title,
                "thumbnail":    thumbnail,
                "permalink":    permalink,
                "status":       status,
                "price":        round(price, 2),
                "canal":        canal_key,
                "brand":        brand,
                "visitas":      visitas,           # None si no hay dato
                "ventas":       ventas,
                "revenue":      round(revenue, 2),
                "orders":       orders,
                "conversion":   round(conversion * 100, 2) if conversion is not None else None,
                "categoria":    categoria,
                "diagnostico":  diagnostico,
                "accion":       accion,
                "prioridad":    prioridad,
                "last_updated": last_updated,
                "window_days":  WINDOW_DAYS,
            })

    return publications

# ──────────────────────────────────────────────────────────────────────────────
# ANÁLISIS BI — build_product_map
# ──────────────────────────────────────────────────────────────────────────────

def build_product_map(all_lines, stock_items, date_from, date_to, weights):
    tn_by_sku = {}
    tn_by_vid = {}
    for vid, item in stock_items.items():
        tn_by_vid[vid] = item
        if item.get("sku"):
            tn_by_sku[item["sku"]] = item

    by_sku = defaultdict(lambda: {
        "titles":    set(),
        "item_ids":  set(),
        "units":     0,
        "revenue":   0.0,
        "by_canal":  defaultdict(lambda: {"units": 0, "revenue": 0.0, "item_ids": set()}),
        "by_month":  defaultdict(lambda: {"units": 0, "revenue": 0.0}),
        "by_week":   defaultdict(lambda: {"units": 0}),
        "by_day":    defaultdict(lambda: {"units": 0, "revenue": 0.0}),
        "orders":    set(),
        "last_sale": None,
        "first_sale":None,
        "thumbnail": "",
        "permalink": "",
    })

    try:
        dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        dt_to   = datetime.strptime(date_to,   "%Y-%m-%d")
        period_days = max((dt_to - dt_from).days, 1)
    except:
        period_days = 365

    for line in all_lines:
        date = line["date"]
        if date < date_from or date > date_to:
            continue

        sku = line["sku"] or line["item_id"]
        p   = by_sku[sku]
        p["item_ids"].add(line["item_id"])
        if line["title"]:
            p["titles"].add(line["title"])
        if line["thumbnail"] and not p["thumbnail"]:
            p["thumbnail"] = line["thumbnail"]
        if line["permalink"] and not p["permalink"]:
            p["permalink"] = line["permalink"]

        p["units"]   += line["qty"]
        p["revenue"] += line["revenue"]
        p["by_canal"][line["canal"]]["units"]   += line["qty"]
        p["by_canal"][line["canal"]]["revenue"] += line["revenue"]
        p["by_canal"][line["canal"]]["item_ids"].add(line["item_id"])
        p["by_month"][line["month"]]["units"]   += line["qty"]
        p["by_month"][line["month"]]["revenue"] += line["revenue"]
        p["by_day"][date]["units"]              += line["qty"]
        p["by_day"][date]["revenue"]            += line["revenue"]

        try:
            week = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-W%V")
        except:
            week = date[:7]
        p["by_week"][week]["units"] += line["qty"]

        p["orders"].add(line["order_id"])
        if p["last_sale"]  is None or date > p["last_sale"]:  p["last_sale"]  = date
        if p["first_sale"] is None or date < p["first_sale"]: p["first_sale"] = date

    all_units = [p["units"] for p in by_sku.values() if p["units"] > 0]
    p90_units = sorted(all_units)[int(len(all_units) * 0.9)] if len(all_units) > 10 else (max(all_units) if all_units else 1)

    results = {}
    for sku, p in by_sku.items():
        units   = p["units"]
        revenue = p["revenue"]

        tn_item = tn_by_sku.get(sku)
        if not tn_item:
            for iid in p["item_ids"]:
                if iid in tn_by_vid:
                    tn_item = tn_by_vid[iid]
                    break

        stock = None
        cost  = 0.0
        price = 0.0
        created_at = ""
        if tn_item:
            raw_stock = tn_item.get("stock")
            stock     = int(raw_stock) if raw_stock is not None else None
            cost      = float(tn_item.get("cost", 0) or 0)
            price     = float(tn_item.get("price", 0) or 0)
            created_at= tn_item.get("created_at", "")

        if price == 0 and units > 0:
            price = revenue / units

        vel_diaria  = units / period_days if period_days > 0 else 0
        vel_mensual = vel_diaria * 30

        dias_stock = None
        if stock is not None and vel_diaria > 0:
            dias_stock = int(stock / vel_diaria)
        elif stock == 0:
            dias_stock = 0

        margen = ((price - cost) / price) if price > 0 and cost > 0 else None

        weeks_sorted = sorted(p["by_week"].keys())
        recent_u = sum(p["by_week"][w]["units"] for w in weeks_sorted[-4:]) if len(weeks_sorted) >= 1 else 0
        prev_u   = sum(p["by_week"][w]["units"] for w in weeks_sorted[-8:-4]) if len(weeks_sorted) >= 5 else 0
        tendencia = ((recent_u - prev_u) / prev_u) if prev_u > 0 else None

        score_ventas = min(units / p90_units, 1.0) if p90_units > 0 else 0

        if margen is not None:
            score_margen = max(0.0, min(margen, 1.0))
        else:
            score_margen = 0.4

        if stock == 0:
            score_rotacion = 0.0
        elif dias_stock is None:
            score_rotacion = 0.4
        elif dias_stock <= 7:
            score_rotacion = dias_stock / 7 * 0.3
        elif dias_stock <= 60:
            score_rotacion = 1.0
        elif dias_stock <= 120:
            score_rotacion = 1.0 - (dias_stock - 60) / 120
        else:
            score_rotacion = 0.2

        if tendencia is not None:
            score_velocidad = min(max((tendencia + 1) / 2, 0), 1)
        else:
            score_velocidad = 0.4

        w = weights
        health = (
            score_ventas    * w["ventas"]   +
            score_margen    * w["margen"]   +
            score_rotacion  * w["rotacion"] +
            score_velocidad * w["velocidad"]
        ) * 100

        if stock == 0:
            stock_status = "quiebre"
        elif stock is None:
            stock_status = "sin_datos"
        elif dias_stock is not None and dias_stock < 14:
            stock_status = "critico"
        elif dias_stock is not None and dias_stock < 30:
            stock_status = "bajo"
        else:
            stock_status = "ok"

        ds = days_since(p["last_sale"])
        if ds is None:        activity = "sin_ventas"
        elif ds <= 30:        activity = "activo"
        elif ds <= 90:        activity = "lento"
        else:                 activity = "inactivo"

        if vel_mensual >= 3:   rot_label = "alta"
        elif vel_mensual >= 0.5: rot_label = "media"
        else:                  rot_label = "baja"

        title = ""
        if tn_item:
            title = tn_item.get("title", "")
        if not title and p["titles"]:
            title = sorted(p["titles"], key=len, reverse=True)[0]
        if not title:
            title = sku

        by_canal_out = {}
        for c, v in p["by_canal"].items():
            by_canal_out[c] = {
                "units":    v["units"],
                "revenue":  round(v["revenue"], 2),
                "item_ids": list(v["item_ids"]),
            }

        # by_day: solo últimos 60 días para no inflar el HTML
        cutoff_day = (dt_to - timedelta(days=60)).strftime("%Y-%m-%d") if 'dt_to' in dir() else date_from
        by_day_out = {
            k: {"units": v["units"], "revenue": round(v["revenue"], 2)}
            for k, v in p["by_day"].items()
            if k >= cutoff_day
        }

        results[sku] = {
            "sku":           sku,
            "title":         title,
            "units":         units,
            "revenue":       round(revenue, 2),
            "orders":        len(p["orders"]),
            "cost":          round(cost, 2),
            "price":         round(price, 2),
            "margen_pct":    round(margen * 100, 1) if margen is not None else None,
            "stock":         stock,
            "dias_stock":    dias_stock,
            "stock_status":  stock_status,
            "vel_diaria":    round(vel_diaria, 4),
            "vel_mensual":   round(vel_mensual, 2),
            "tendencia":     round(tendencia * 100, 1) if tendencia is not None else None,
            "health":        round(health, 1),
            "activity":      activity,
            "last_sale":     p["last_sale"] or "--",
            "first_sale":    p["first_sale"] or "--",
            "created_at":    created_at,
            "rot_label":     rot_label,
            "thumbnail":     p["thumbnail"],
            "permalink":     p["permalink"],
            "by_canal":      by_canal_out,
            "by_month":      {k: {"units": v["units"], "revenue": round(v["revenue"], 2)}
                              for k, v in p["by_month"].items()},
            "by_day":        by_day_out,
        }

    return results

# ──────────────────────────────────────────────────────────────────────────────
# PLANIFICADOR DE COMPRA
# ──────────────────────────────────────────────────────────────────────────────

def build_purchase_plan(products, lead_time_days=21, safety_stock_days=14):
    """
    4 categorías de urgencia:
      urgente   — stock = 0, con buen ritmo de ventas
      critico   — días stock < lead_time (21d), buen ritmo (vel >= 1/mes)
      planificar — días stock 21–35d, cualquier ritmo
      analizar  — días stock 35d–30d pero vel < 1/mes (stock quieto pronto)
    """
    plan = []

    for sku, p in products.items():
        ds = days_since(p["last_sale"])
        if ds is None or ds > 90:
            continue

        vel = p["vel_mensual"] / 30  # vel diaria
        if vel <= 0:
            continue

        stock      = p["stock"] if p["stock"] is not None else 0
        dias_stock = p["dias_stock"] if p["dias_stock"] is not None else (stock / vel if vel > 0 else 999)

        qty_needed = max(0, round((lead_time_days + safety_stock_days) * vel - stock))

        vel_mensual = p["vel_mensual"]

        if dias_stock <= 0:
            urgency = "urgente"    # quebrado pero vende
        elif dias_stock < lead_time_days:
            urgency = "critico"    # quiebre inminente < 21d
        elif dias_stock < lead_time_days + safety_stock_days:
            urgency = "planificar" # quiebre en 21–35d
        elif dias_stock < 60 and vel_mensual < 1:
            urgency = "analizar"   # quiebre < 60d pero vel baja, analizar si reponer
        else:
            continue  # OK, no necesita acción

        if qty_needed == 0 and urgency not in ("urgente", "critico"):
            qty_needed = max(1, round(lead_time_days * vel))

        plan.append({
            "sku":         sku,
            "title":       p["title"],
            "stock":       stock,
            "dias_stock":  round(dias_stock),
            "vel_mensual": p["vel_mensual"],
            "qty_sugerida":qty_needed,
            "urgency":     urgency,
            "last_sale":   p["last_sale"],
            "cost":        p["cost"],
            "revenue":     p["revenue"],
            "inversion":   round(qty_needed * p["cost"], 2) if p["cost"] > 0 else None,
        })

    order = {"urgente": 0, "critico": 1, "planificar": 2, "analizar": 3}
    plan.sort(key=lambda x: (order.get(x["urgency"], 9), x["dias_stock"]))
    return plan

# ──────────────────────────────────────────────────────────────────────────────
# HTML GENERATOR
# ──────────────────────────────────────────────────────────────────────────────

def generate_html(products, monthly_totals, daily_totals, canal_totals, active_no_sales,
                  purchase_plan, config, date_from, date_to, sync_state,
                  ml_publications=None):

    weights   = config["settings"]["health_score_weights"]
    generated = datetime.now().strftime("%d/%m/%Y %H:%M")

    prods_list = sorted(products.values(), key=lambda p: p["revenue"], reverse=True)
    quiebre    = [p for p in prods_list if p["stock_status"] == "quiebre" and p["units"] > 0]
    discontinuados = [p for p in prods_list
                      if p["stock_status"] == "quiebre"
                      and days_since(p["last_sale"]) is not None
                      and days_since(p["last_sale"]) > 90
                      and p["vel_mensual"] >= 0.5]

    sin_ventas = sorted(active_no_sales, key=lambda p: p.get("title", ""))

    period_label = f"{date_from} → {date_to}"
    now          = datetime.now()
    last30_from  = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    last30_to    = now.strftime("%Y-%m-%d")

    last30_prods = {sku: p for sku, p in products.items()
                    if p["last_sale"] and p["last_sale"] >= last30_from}
    ov_revenue   = sum(
        sum(v["revenue"] for m, v in p["by_month"].items() if m >= last30_from[:7])
        for p in prods_list
    )
    ov_units     = sum(
        sum(v["units"] for m, v in p["by_month"].items() if m >= last30_from[:7])
        for p in prods_list
    )
    ov_orders_30 = sum(
        1 for p in prods_list
        for m, v in p["by_month"].items()
        if m >= last30_from[:7]
    )
    ov_ticket    = round(ov_revenue / max(ov_orders_30, 1))
    margins_all  = [p["margen_pct"] for p in prods_list if p["margen_pct"] is not None]
    ov_margen    = round(sum(margins_all) / len(margins_all), 1) if margins_all else 0
    ov_skus_activos = len(prods_list)

    months_sorted = sorted(monthly_totals.keys())
    canal_labels  = {k: v["label"] for k, v in config["channels"].items() if v.get("enabled")}

    total_rev   = sum(p["revenue"] for p in prods_list)
    top20pct_n  = max(1, len(prods_list) // 5)
    top20pct_rev= sum(p["revenue"] for p in prods_list[:top20pct_n])
    top20pct_pct= round(top20pct_rev / total_rev * 100, 1) if total_rev > 0 else 0

    sync_info = " · ".join(
        f"{canal_labels.get(k, k)}: {v.get('last_sync','')[:16].replace('T',' ')}"
        for k, v in (sync_state or {}).items()
        if k in canal_labels
    )

    # ML Publications JSON
    ml_pubs = ml_publications or []

    # Estadísticas de categorías ML
    ml_muerto     = len([p for p in ml_pubs if p["categoria"] == "muerto"])
    ml_invisible  = len([p for p in ml_pubs if p["categoria"] == "invisible"])
    ml_oportunidad= len([p for p in ml_pubs if p["categoria"] == "oportunidad"])
    ml_ganador    = len([p for p in ml_pubs if p["categoria"] == "ganador"])

    # JSON payloads
    products_json     = json.dumps(prods_list,       ensure_ascii=False)
    no_sales_json     = json.dumps(sin_ventas,       ensure_ascii=False)
    quiebre_json      = json.dumps(quiebre,          ensure_ascii=False)
    disco_json        = json.dumps(discontinuados,   ensure_ascii=False)
    purchase_json     = json.dumps(purchase_plan,    ensure_ascii=False)
    monthly_json      = json.dumps({m: monthly_totals[m] for m in months_sorted}, ensure_ascii=False)
    canal_totals_json = json.dumps(canal_totals,     ensure_ascii=False)
    canal_labels_json = json.dumps(canal_labels,     ensure_ascii=False)
    months_keys_json  = json.dumps(months_sorted,    ensure_ascii=False)
    daily_totals_json = json.dumps(daily_totals,    ensure_ascii=False)
    ml_pubs_json      = json.dumps(ml_pubs,          ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BI Dashboard — Pret a Home / Casa Lavan</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=Inter:wght@300;400;500;600&display=swap');
:root{{
  --bg:#08090a;--s1:#111214;--s2:#18191c;--s3:#1f2023;--s4:#26272b;
  --border:#2a2b2f;--border2:#35363b;
  --text:#ecedef;--text2:#8a8c94;--text3:#5a5c64;
  --gold:#f0b429;--gold2:#c98a1a;
  --green:#34d399;--red:#f87171;--blue:#60a5fa;--purple:#a78bfa;--orange:#fb923c;
  --drawer-w:260px;
}}
*{{box-sizing:border-box;margin:0;padding:0;}}
html{{scroll-behavior:smooth;}}
body{{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;font-size:13px;line-height:1.5;overflow-x:hidden;}}

/* ── TOPBAR ── */
.topbar{{
  background:var(--s1);border-bottom:1px solid var(--border);
  padding:0 20px;display:flex;align-items:center;
  position:sticky;top:0;z-index:200;height:50px;gap:14px;
}}
.brand{{font-family:'Syne',sans-serif;font-weight:800;font-size:15px;white-space:nowrap;letter-spacing:-.3px;}}
.brand .sep{{color:var(--text3);margin:0 5px;}}
.topbar-right{{margin-left:auto;font-size:10px;color:var(--text3);white-space:nowrap;}}
.current-page-label{{
  font-size:12px;font-weight:600;color:var(--text2);letter-spacing:.5px;
  text-transform:uppercase;flex:1;text-align:center;
}}

/* ── HAMBURGER BUTTON ── */
.hamburger{{
  background:none;border:none;cursor:pointer;padding:6px;
  display:flex;flex-direction:column;gap:5px;border-radius:6px;
  transition:background .15s;flex-shrink:0;
}}
.hamburger:hover{{background:var(--s3);}}
.hamburger span{{
  display:block;width:20px;height:2px;background:var(--text2);
  border-radius:2px;transition:all .25s cubic-bezier(.4,0,.2,1);
}}
.hamburger.open span:nth-child(1){{transform:translateY(7px) rotate(45deg);background:var(--gold);}}
.hamburger.open span:nth-child(2){{opacity:0;transform:scaleX(0);}}
.hamburger.open span:nth-child(3){{transform:translateY(-7px) rotate(-45deg);background:var(--gold);}}

/* ── DRAWER OVERLAY ── */
.drawer-overlay{{
  position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:300;
  opacity:0;pointer-events:none;transition:opacity .25s;
  backdrop-filter:blur(2px);
}}
.drawer-overlay.open{{opacity:1;pointer-events:all;}}

/* ── DRAWER PANEL ── */
.drawer{{
  position:fixed;left:0;top:0;bottom:0;width:var(--drawer-w);
  background:var(--s1);border-right:1px solid var(--border);
  z-index:400;transform:translateX(-100%);
  transition:transform .28s cubic-bezier(.4,0,.2,1);
  display:flex;flex-direction:column;overflow:hidden;
}}
.drawer.open{{transform:translateX(0);}}

.drawer-header{{
  padding:18px 20px 14px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;
}}
.drawer-brand{{font-family:'Syne',sans-serif;font-weight:800;font-size:14px;}}
.drawer-brand .sep{{color:var(--text3);margin:0 4px;}}
.drawer-close{{
  background:none;border:none;color:var(--text3);cursor:pointer;
  font-size:18px;padding:2px 6px;border-radius:4px;line-height:1;
  transition:color .15s;
}}
.drawer-close:hover{{color:var(--text);}}

.drawer-sync{{
  padding:8px 20px;font-size:10px;color:var(--text3);
  border-bottom:1px solid var(--border);line-height:1.6;
}}

.drawer-nav{{flex:1;overflow-y:auto;padding:8px 0;}}
.drawer-section{{
  padding:6px 20px 4px;font-size:9px;font-weight:700;
  color:var(--text3);text-transform:uppercase;letter-spacing:1.5px;
  margin-top:8px;
}}
.drawer-item{{
  display:flex;align-items:center;gap:12px;width:100%;
  background:none;border:none;color:var(--text2);
  padding:10px 20px;cursor:pointer;font-family:'Inter',sans-serif;
  font-size:13px;font-weight:400;text-align:left;
  transition:all .15s;position:relative;
}}
.drawer-item:hover{{background:var(--s2);color:var(--text);}}
.drawer-item.active{{
  background:rgba(240,180,41,.06);color:var(--gold);font-weight:500;
}}
.drawer-item.active::before{{
  content:'';position:absolute;left:0;top:0;bottom:0;width:3px;
  background:var(--gold);border-radius:0 2px 2px 0;
}}
.drawer-item .di-icon{{font-size:16px;width:20px;text-align:center;flex-shrink:0;}}
.drawer-item .di-badge{{
  margin-left:auto;background:rgba(240,180,41,.15);color:var(--gold);
  padding:1px 6px;border-radius:10px;font-size:10px;font-weight:600;
}}
.drawer-item .di-badge.red{{background:rgba(248,113,113,.15);color:var(--red);}}
.drawer-item .di-badge.purple{{background:rgba(167,139,250,.15);color:var(--purple);}}

.drawer-footer{{
  padding:14px 20px;border-top:1px solid var(--border);
  font-size:10px;color:var(--text3);
}}

/* ── MAIN CONTENT ── */
.main-content{{padding-top:0;}}

.gf{{background:var(--s2);border-bottom:1px solid var(--border);padding:8px 24px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;}}
.gf-label{{font-size:10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap;}}
select,input[type=date],input[type=text],input[type=number]{{background:var(--s3);border:1px solid var(--border2);color:var(--text);padding:5px 9px;border-radius:6px;font-size:12px;font-family:'Inter',sans-serif;outline:none;}}
select:focus,input:focus{{border-color:var(--gold);}}
.btn{{background:var(--s3);border:1px solid var(--border2);color:var(--text2);padding:5px 11px;border-radius:6px;cursor:pointer;font-size:11px;font-family:inherit;transition:all .15s;}}
.btn:hover{{color:var(--text);border-color:var(--text2);}}
.btn-gold{{background:var(--gold);border-color:var(--gold);color:#000;font-weight:600;}}
.btn-gold:hover{{background:var(--gold2);}}

.page{{display:none;padding:24px;min-height:calc(100vh - 100px);}}
.page.active{{display:block;}}

#page-sku-detail{{background:var(--bg);}}

.g5{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:20px;}}
.g4{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px;}}
.g3{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px;}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:20px;}}
.g21{{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-bottom:20px;}}

.card{{background:var(--s2);border:1px solid var(--border);border-radius:10px;padding:16px;}}
.card h3{{font-size:10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;}}
.kcard{{background:var(--s2);border:1px solid var(--border);border-radius:10px;padding:14px 16px;}}
.klabel{{font-size:10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.8px;margin-bottom:4px;}}
.kvalue{{font-family:'Syne',sans-serif;font-size:26px;font-weight:700;letter-spacing:-1px;color:var(--gold);line-height:1.1;}}
.kvalue.g{{color:var(--green);}} .kvalue.r{{color:var(--red);}} .kvalue.b{{color:var(--blue);}} .kvalue.p{{color:var(--purple);}}
.ksub{{font-size:10px;color:var(--text3);margin-top:3px;}}
.period-badge{{display:inline-block;background:rgba(240,180,41,.1);color:var(--gold);padding:1px 7px;border-radius:3px;font-size:10px;font-weight:500;margin-left:6px;}}

.stitle{{font-family:'Syne',sans-serif;font-size:12px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:var(--text2);margin-bottom:12px;}}

table{{width:100%;border-collapse:collapse;font-size:12px;}}
thead th{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--border);color:var(--text3);font-size:10px;font-weight:600;letter-spacing:1px;text-transform:uppercase;white-space:nowrap;cursor:pointer;user-select:none;}}
thead th:hover{{color:var(--gold);}}
tbody tr{{border-bottom:1px solid var(--border);cursor:pointer;transition:background .1s;}}
tbody tr:hover{{background:var(--s3);}}
tbody td{{padding:9px 9px;vertical-align:middle;}}
.tr{{text-align:right;}} .tra{{text-align:right;color:var(--gold);font-weight:500;}}

.pcell{{display:flex;align-items:center;gap:9px;}}
.pthumb{{width:36px;height:36px;object-fit:cover;border-radius:5px;background:var(--s3);flex-shrink:0;}}
.pthumb-e{{width:36px;height:36px;border-radius:5px;background:var(--s3);flex-shrink:0;}}
.ptitle{{font-weight:500;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.ptitle a{{color:var(--text);text-decoration:none;}}
.ptitle a:hover{{color:var(--gold);}}
.psku{{font-size:10px;color:var(--text3);}}
.rbar{{height:2px;background:var(--border);border-radius:1px;margin-top:3px;width:100%;}}
.rfill{{height:100%;background:var(--gold);border-radius:1px;opacity:.5;}}

.badge{{display:inline-flex;align-items:center;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600;white-space:nowrap;}}
.bg{{background:rgba(52,211,153,.1);color:var(--green);}}
.by{{background:rgba(240,180,41,.1);color:var(--gold);}}
.br{{background:rgba(248,113,113,.1);color:var(--red);}}
.bb{{background:rgba(96,165,250,.1);color:var(--blue);}}
.bgr{{background:rgba(138,140,148,.08);color:var(--text2);}}
.bp{{background:rgba(167,139,250,.1);color:var(--purple);}}
.bo{{background:rgba(251,146,60,.1);color:var(--orange);}}

.hring{{display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;font-family:'Syne',sans-serif;font-size:11px;font-weight:700;flex-shrink:0;}}
.he{{background:rgba(52,211,153,.12);color:var(--green);border:1.5px solid rgba(52,211,153,.3);}}
.hg{{background:rgba(240,180,41,.1);color:var(--gold);border:1.5px solid rgba(240,180,41,.2);}}
.hf{{background:rgba(251,146,60,.1);color:var(--orange);border:1.5px solid rgba(251,146,60,.2);}}
.hp{{background:rgba(248,113,113,.08);color:var(--red);border:1.5px solid rgba(248,113,113,.15);}}

.cpill{{display:inline-flex;align-items:center;padding:1px 5px;border-radius:3px;font-size:10px;font-weight:500;margin:1px;}}
.cml{{background:rgba(240,180,41,.08);color:var(--gold);}}
.ctn{{background:rgba(96,165,250,.08);color:var(--blue);}}

.pag{{display:flex;gap:4px;justify-content:center;margin-top:14px;align-items:center;flex-wrap:wrap;}}
.pag button{{background:var(--s2);border:1px solid var(--border);color:var(--text);padding:3px 9px;border-radius:5px;cursor:pointer;font-size:11px;font-family:inherit;}}
.pag button.active{{background:var(--gold);border-color:var(--gold);color:#000;font-weight:600;}}
.pag button:disabled{{opacity:.3;cursor:default;}}
.pag .pi{{color:var(--text3);font-size:10px;padding:0 3px;}}

.ns-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px;}}
.ns-card{{background:var(--s2);border:1px solid var(--border);border-radius:8px;padding:12px;display:flex;gap:10px;}}
.ns-card img,.ns-img{{width:44px;height:44px;object-fit:cover;border-radius:5px;background:var(--s3);flex-shrink:0;}}
.ns-info .nst{{font-size:12px;font-weight:500;line-height:1.3;}}
.ns-info .nst a{{color:var(--text);text-decoration:none;}}
.ns-info .nst a:hover{{color:var(--gold);}}
.ns-info .nsm{{font-size:11px;color:var(--text3);margin-top:3px;}}

.filters{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;align-items:center;}}

/* SKU Detail */
.sku-header{{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:20px 24px;margin-bottom:20px;display:flex;gap:20px;align-items:flex-start;}}
.sku-thumb{{width:80px;height:80px;object-fit:cover;border-radius:8px;background:var(--s3);flex-shrink:0;}}
.sku-thumb-e{{width:80px;height:80px;border-radius:8px;background:var(--s3);flex-shrink:0;}}
.sku-info h2{{font-family:'Syne',sans-serif;font-size:20px;font-weight:700;margin-bottom:4px;}}
.sku-info .sku-code{{font-size:12px;color:var(--text3);margin-bottom:8px;}}
.sku-links{{display:flex;gap:8px;flex-wrap:wrap;}}
.sku-link{{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:5px;font-size:11px;font-weight:500;text-decoration:none;border:1px solid var(--border2);color:var(--text2);background:var(--s2);}}
.sku-link:hover{{color:var(--gold);border-color:var(--gold);}}
.sku-link.ml{{border-color:rgba(240,180,41,.3);color:var(--gold);}}
.sku-link.tn{{border-color:rgba(96,165,250,.3);color:var(--blue);}}

.back-btn{{display:inline-flex;align-items:center;gap:6px;background:var(--s2);border:1px solid var(--border);color:var(--text2);padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px;font-family:inherit;margin-bottom:16px;}}
.back-btn:hover{{color:var(--text);}}

.u-urgente{{color:var(--red);font-weight:600;}}
.u-critico{{color:var(--orange);font-weight:500;}}
.u-planificar{{color:var(--gold);}}
.u-ok{{color:var(--green);}}

/* ── ML PUBLICACIONES ── */
.ml-scorecard{{
  display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px;
}}
.ml-cat-card{{
  border-radius:10px;padding:16px;border:1px solid;
  position:relative;overflow:hidden;
}}
.ml-cat-card::before{{
  content:'';position:absolute;inset:0;opacity:.04;
}}
.ml-cat-card.muerto{{border-color:#444;background:rgba(33,33,33,.4);}}
.ml-cat-card.invisible{{border-color:rgba(248,113,113,.3);background:rgba(248,113,113,.05);}}
.ml-cat-card.oportunidad{{border-color:rgba(240,180,41,.3);background:rgba(240,180,41,.05);}}
.ml-cat-card.ganador{{border-color:rgba(52,211,153,.3);background:rgba(52,211,153,.05);}}
.ml-cat-label{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;}}
.ml-cat-card.muerto .ml-cat-label{{color:#888;}}
.ml-cat-card.invisible .ml-cat-label{{color:var(--red);}}
.ml-cat-card.oportunidad .ml-cat-label{{color:var(--gold);}}
.ml-cat-card.ganador .ml-cat-label{{color:var(--green);}}
.ml-cat-count{{font-family:'Syne',sans-serif;font-size:34px;font-weight:800;line-height:1;}}
.ml-cat-card.muerto .ml-cat-count{{color:#666;}}
.ml-cat-card.invisible .ml-cat-count{{color:var(--red);}}
.ml-cat-card.oportunidad .ml-cat-count{{color:var(--gold);}}
.ml-cat-card.ganador .ml-cat-count{{color:var(--green);}}
.ml-cat-desc{{font-size:11px;color:var(--text3);margin-top:4px;}}

.ml-cat-badge{{
  display:inline-flex;align-items:center;gap:4px;padding:2px 8px;
  border-radius:4px;font-size:10px;font-weight:700;white-space:nowrap;
}}
.ml-cat-badge.muerto{{background:rgba(68,68,68,.3);color:#aaa;}}
.ml-cat-badge.invisible{{background:rgba(248,113,113,.12);color:var(--red);}}
.ml-cat-badge.oportunidad{{background:rgba(240,180,41,.12);color:var(--gold);}}
.ml-cat-badge.ganador{{background:rgba(52,211,153,.12);color:var(--green);}}

.ml-row-expand{{
  background:var(--s3);border-top:none;
}}
.ml-expand-content{{
  padding:12px 16px;font-size:12px;
  display:grid;grid-template-columns:1fr 1fr;gap:10px;
}}
.ml-expand-item{{}}
.ml-expand-label{{font-size:10px;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px;}}
.ml-expand-value{{color:var(--text2);line-height:1.4;}}

.prio-filter{{display:flex;gap:6px;flex-wrap:wrap;}}
.prio-btn{{
  background:var(--s3);border:1px solid var(--border2);color:var(--text2);
  padding:5px 12px;border-radius:20px;cursor:pointer;font-size:11px;
  font-family:inherit;transition:all .15s;font-weight:500;
}}
.prio-btn:hover{{color:var(--text);border-color:var(--text2);}}
.prio-btn.active{{font-weight:600;}}
.prio-btn.all.active{{background:var(--s4);color:var(--text);border-color:var(--text2);}}
.prio-btn.muerto.active{{background:rgba(68,68,68,.3);color:#aaa;border-color:#555;}}
.prio-btn.invisible.active{{background:rgba(248,113,113,.12);color:var(--red);border-color:rgba(248,113,113,.3);}}
.prio-btn.oportunidad.active{{background:rgba(240,180,41,.12);color:var(--gold);border-color:rgba(240,180,41,.3);}}
.prio-btn.ganador.active{{background:rgba(52,211,153,.12);color:var(--green);border-color:rgba(52,211,153,.3);}}
</style>
</head>
<body>

<!-- ── DRAWER OVERLAY ── -->
<div class="drawer-overlay" id="drawerOverlay" onclick="closeDrawer()"></div>

<!-- ── NAVIGATION DRAWER ── -->
<nav class="drawer" id="drawer">
  <div class="drawer-header">
    <div class="drawer-brand">Pret a Home <span class="sep">/</span> Casa Lavan</div>
    <button class="drawer-close" onclick="closeDrawer()">✕</button>
  </div>
  <div class="drawer-sync" id="drawer-sync-info">{sync_info}</div>
  <div class="drawer-nav">
    <div class="drawer-section">Análisis</div>
    <button class="drawer-item active" data-page="overview" onclick="showPage('overview',this)">
      <span class="di-icon">📊</span> Overview
    </button>
    <button class="drawer-item" data-page="productos" onclick="showPage('productos',this)">
      <span class="di-icon">📦</span> Productos
    </button>
    <button class="drawer-item" data-page="sku-detail" id="tab-sku-detail" onclick="showPage('sku-detail',this)">
      <span class="di-icon">🔍</span> SKU Detail
    </button>
    <button class="drawer-item" data-page="bi" onclick="showPage('bi',this)">
      <span class="di-icon">📈</span> BI &amp; Métricas
    </button>

    <div class="drawer-section">Stock</div>
    <button class="drawer-item" data-page="sin-ventas" onclick="showPage('sin-ventas',this)">
      <span class="di-icon">🫥</span> Sin Ventas
    </button>
    <button class="drawer-item" data-page="quiebre" onclick="showPage('quiebre',this)">
      <span class="di-icon">⚠️</span> Quiebre Stock
      <span class="di-badge red" id="drawer-quiebre-count">{len(quiebre)}</span>
    </button>
    <button class="drawer-item" data-page="planificador" onclick="showPage('planificador',this)">
      <span class="di-icon">🛒</span> Planificador
    </button>

    <div class="drawer-section">Mercado Libre</div>
    <button class="drawer-item" data-page="ml-pubs" onclick="showPage('ml-pubs',this)">
      <span class="di-icon">🛍️</span> Publicaciones ML
      <span class="di-badge purple" id="drawer-mlpubs-count">{len(ml_pubs)}</span>
    </button>
  </div>
  <div class="drawer-footer">
    Generado {generated}
  </div>
</nav>

<!-- ── TOPBAR ── -->
<div class="topbar">
  <button class="hamburger" id="hamburgerBtn" onclick="toggleDrawer()" aria-label="Menú">
    <span></span><span></span><span></span>
  </button>
  <div class="brand">Pret <span class="sep">/</span> Lavan</div>
  <div class="current-page-label" id="currentPageLabel">Overview</div>
  <div class="topbar-right" id="topbar-sync">{sync_info[:50] if sync_info else ''}</div>
</div>

<!-- FILTROS GLOBALES -->
<div class="gf" id="global-filters">
  <span class="gf-label">Período</span>
  <input type="date" id="fDesde" value="{date_from}">
  <span style="color:var(--text3)">→</span>
  <input type="date" id="fHasta" value="{date_to}">
  <span class="gf-label" style="margin-left:8px">Canal</span>
  <select id="fCanal">
    <option value="">Todos</option>
    <option value="ml_pret">ML Pret</option>
    <option value="tn_pret">TN Pret</option>
    <option value="tn_lavan">TN Lavan</option>
  </select>
  <span class="gf-label">Marca</span>
  <select id="fMarca">
    <option value="">Todas</option>
    <option value="pret">Pret a Home</option>
    <option value="lavan">Casa Lavan</option>
  </select>
  <button class="btn btn-gold" onclick="applyGlobalFilters()">Aplicar</button>
  <button class="btn" onclick="resetGlobalFilters()">Reset</button>
  <span id="gf-status" style="font-size:10px;color:var(--text3)"></span>
</div>

<!-- ══════════════════════════════════════════ OVERVIEW ══ -->
<div id="page-overview" class="page active">
  <!-- KPIs -->
  <div class="g5">
    <div class="kcard"><div class="klabel">Revenue <span class="period-badge">últimos 30d</span></div><div class="kvalue" id="ov-revenue">—</div></div>
    <div class="kcard"><div class="klabel">Unidades <span class="period-badge">últimos 30d</span></div><div class="kvalue b" id="ov-units">—</div></div>
    <div class="kcard"><div class="klabel">Ticket promedio <span class="period-badge">últimos 30d</span></div><div class="kvalue" id="ov-ticket">—</div><div class="ksub">revenue / órdenes</div></div>
    <div class="kcard"><div class="klabel">Margen promedio <span class="period-badge">últimos 30d</span></div><div class="kvalue g" id="ov-margen">—</div></div>
    <div class="kcard"><div class="klabel">SKUs publicados</div><div class="kvalue p" id="ov-skus">—</div><div class="ksub" id="ov-sinventas"></div></div>
  </div>

  <!-- Chart 10 días — el primero y principal -->
  <div class="card" style="margin-bottom:14px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h3 style="margin-bottom:0">Revenue por canal — últimos 10 días</h3>
      <span style="font-size:10px;color:var(--text3)" id="ov-10d-range"></span>
    </div>
    <canvas id="chart10d" height="130"></canvas>
  </div>

  <!-- Revenue mensual + distribución -->
  <div class="g21">
    <div class="card"><h3>Revenue mensual por canal <span class="period-badge" id="ov-period-label">{period_label}</span></h3><canvas id="chartRevenue" height="170"></canvas></div>
    <div class="card"><h3>Distribución por canal</h3><canvas id="chartCanal" height="170"></canvas></div>
  </div>

  <!-- Health Score de Stock -->
  <div class="card" style="margin-bottom:14px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <h3 style="margin-bottom:0">Health Score de Stock — ¿qué porcentaje del stock está trabajando?</h3>
      <span style="font-size:10px;color:var(--text3)">Por SKU con stock &gt; 0</span>
    </div>
    <!-- Barra apilada visual -->
    <div style="display:flex;height:18px;border-radius:6px;overflow:hidden;margin-bottom:14px;gap:1px" id="stock-health-bar"></div>
    <!-- Grid de categorías -->
    <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px" id="stock-health-grid"></div>
    <div style="margin-top:12px;font-size:10px;color:var(--text3);line-height:1.8">
      <strong style="color:var(--text2)">Criterios:</strong>
      🔴 Crítico: &gt;30d quieto y &gt;50 uni ·
      🟠 Muy malo: &gt;30d quieto y 30–50 uni ·
      🟡 Malo: &gt;30d quieto y &lt;30 uni ·
      ⚪ Regular: con ventas, quiebre &gt;30d ·
      🟢 Bueno: quiebre 15–30d ·
      ✨ Excelente: quiebre &lt;15d
    </div>
  </div>

  <!-- Pareto + estado stock -->
  <div class="g2">
    <div class="card" style="cursor:pointer" onclick="showPage('bi',document.querySelector('[data-page=bi]'))">
      <h3>Concentración de revenue <span class="period-badge" id="pareto-period">{period_label}</span></h3>
      <div style="font-size:32px;font-family:'Syne',sans-serif;font-weight:800;color:var(--gold)" id="pareto-pct">—</div>
      <div style="font-size:12px;color:var(--text2);margin-top:2px" id="pareto-desc"></div>
      <div style="height:6px;background:var(--s3);border-radius:3px;margin:10px 0"><div id="pb-fill" style="height:100%;background:var(--gold);border-radius:3px;"></div></div>
      <div id="top5-list"></div>
      <div style="font-size:11px;color:var(--text3);margin-top:8px">→ Ver lista completa en BI &amp; Métricas</div>
    </div>
    <div class="card"><h3>Estado de stock</h3><div id="stock-summary" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:4px"></div></div>
  </div>
</div>

<!-- ══════════════════════════════════════════ PRODUCTOS ══ -->
<div id="page-productos" class="page">
  <div class="filters">
    <input style="width:200px" type="text" id="pSearch" placeholder="🔍 SKU o nombre..." oninput="filterProducts()">
    <select id="pSort" onchange="filterProducts()">
      <option value="revenue">Revenue ↓</option>
      <option value="units">Unidades ↓</option>
      <option value="health">Health ↓</option>
      <option value="vel_mensual">Velocidad ↓</option>
      <option value="margen_pct">Margen ↓</option>
      <option value="dias_stock">Días stock ↓</option>
    </select>
    <select id="pStockF" onchange="filterProducts()">
      <option value="">Stock: Todos</option>
      <option value="quiebre">Quiebre (0)</option>
      <option value="critico">Crítico (&lt;14d)</option>
      <option value="bajo">Bajo (&lt;30d)</option>
      <option value="ok">OK</option>
    </select>
    <select id="pActF" onchange="filterProducts()">
      <option value="">Actividad: Todos</option>
      <option value="activo">Activo (&lt;30d)</option>
      <option value="lento">Lento (30-90d)</option>
      <option value="inactivo">Inactivo (&gt;90d)</option>
    </select>
    <input type="number" id="pMinRev" placeholder="Rev. mín $" oninput="filterProducts()" style="width:80px">
    <button class="btn" onclick="clearProdFilters()">Limpiar</button>
    <span id="p-count" style="font-size:11px;color:var(--text3);margin-left:auto"></span>
  </div>
  <div style="font-size:11px;color:var(--text3);margin-bottom:10px">Revenue y unidades del período seleccionado: <strong style="color:var(--text2)" id="prod-period-label">{period_label}</strong>. Clic en una fila para ver detalle del SKU.</div>
  <table>
    <thead><tr>
      <th onclick="sortProds('rank')">#</th>
      <th>Producto / SKU</th>
      <th onclick="sortProds('health')">Health</th>
      <th class="tr" onclick="sortProds('revenue')">Revenue</th>
      <th class="tr" onclick="sortProds('units')">Uni.</th>
      <th class="tr" onclick="sortProds('vel_mensual')">Vel/mes</th>
      <th class="tr" onclick="sortProds('margen_pct')">Margen</th>
      <th class="tr" onclick="sortProds('stock')">Stock</th>
      <th class="tr" onclick="sortProds('dias_stock')">Días stock</th>
      <th class="tr" onclick="sortProds('last_sale')">Última venta</th>
      <th>Canales</th>
    </tr></thead>
    <tbody id="prod-tbody"></tbody>
  </table>
  <div class="pag" id="prod-pag"></div>
</div>

<!-- ══════════════════════════════════════════ SKU DETAIL ══ -->
<div id="page-sku-detail" class="page">
  <div style="display:flex;gap:10px;align-items:center;margin-bottom:20px">
    <div style="position:relative;flex:1;max-width:400px">
      <input type="text" id="skuSearch" placeholder="🔍 Buscar SKU o nombre de producto..."
        oninput="searchSKU(this.value)"
        style="width:100%;padding:10px 14px;background:var(--s2);border:1px solid var(--border2);color:var(--text);border-radius:8px;font-size:13px;font-family:inherit;outline:none;">
      <div id="skuSuggestions" style="display:none;position:absolute;top:100%;left:0;right:0;background:var(--s2);border:1px solid var(--border2);border-radius:0 0 8px 8px;z-index:50;max-height:280px;overflow-y:auto"></div>
    </div>
    <button class="back-btn" id="sku-back-btn" onclick="goBack()" style="display:none">← Volver</button>
  </div>
  <div id="sku-empty-state" style="text-align:center;padding:60px 20px;color:var(--text3)">
    <div style="font-size:40px;margin-bottom:12px">🔍</div>
    <div style="font-size:15px;color:var(--text2);margin-bottom:6px">Buscá un SKU o producto</div>
    <div style="font-size:12px">Escribí el SKU, nombre o parte del título para ver el detalle</div>
  </div>
  <div id="sku-detail-content" style="display:none">
    <div id="sku-header-content"></div>
    <div class="g4" id="sku-kpis"></div>
    <div class="g2">
      <div class="card"><h3 id="sku-monthly-title">Ventas por mes</h3><canvas id="skuChartMonthly" height="200"></canvas></div>
      <div class="card"><h3>Ventas por canal</h3><canvas id="skuChartCanal" height="200"></canvas></div>
    </div>
    <div class="g2">
      <div class="card"><h3>Publicaciones</h3><div id="sku-publications"></div></div>
      <div class="card"><h3>Info de producto</h3><div id="sku-info-table"></div></div>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════ SIN VENTAS ══ -->
<div id="page-sin-ventas" class="page">
  <div style="font-size:11px;color:var(--text3);margin-bottom:12px;padding:8px 12px;background:var(--s2);border-radius:6px;border:1px solid var(--border)">
    Publicaciones activas del catálogo (TN Pret) sin ninguna venta en el período. Por defecto se muestran solo las publicadas hace más de 30 días.
  </div>
  <div class="filters">
    <input style="width:200px" type="text" id="nsSearch" placeholder="🔍 Buscar..." oninput="filterNoSales()">
    <select id="nsStockF" onchange="filterNoSales()">
      <option value="">Stock: Todos</option>
      <option value="con">Con stock (&gt;0)</option>
      <option value="sin">Sin stock (=0)</option>
    </select>
    <select id="nsAge" onchange="filterNoSales()">
      <option value="30">Publicado hace +30 días</option>
      <option value="60">Publicado hace +60 días</option>
      <option value="90">Publicado hace +90 días</option>
      <option value="0">Todos (incluso recientes)</option>
    </select>
    <select id="nsSort" onchange="filterNoSales()">
      <option value="title">A-Z</option>
      <option value="stock_desc">Stock ↓</option>
      <option value="price_desc">Precio ↓</option>
      <option value="age_desc">Más antiguos primero</option>
      <option value="notselling">Sin ventas más tiempo</option>
    </select>
    <span id="ns-count" style="font-size:11px;color:var(--text3);margin-left:auto"></span>
  </div>
  <div class="ns-grid" id="ns-grid"></div>
  <div class="pag" id="ns-pag"></div>
</div>

<!-- ══════════════════════════════════════════ QUIEBRE ══ -->
<div id="page-quiebre" class="page">
  <div style="display:flex;gap:8px;margin-bottom:16px;border-bottom:1px solid var(--border);padding-bottom:0;">
    <button class="nav-tab active" style="height:36px;background:none;border:none;border-bottom:2px solid var(--gold);color:var(--gold);padding:0 14px;cursor:pointer;font-family:inherit;font-size:11px;font-weight:500;letter-spacing:.5px;text-transform:uppercase;" onclick="switchQTab('activos',this)" id="qtab-activos">Con ventas recientes</button>
    <button class="nav-tab" style="height:36px;background:none;border:none;border-bottom:2px solid transparent;color:var(--text2);padding:0 14px;cursor:pointer;font-family:inherit;font-size:11px;font-weight:500;letter-spacing:.5px;text-transform:uppercase;" onclick="switchQTab('disco',this)" id="qtab-disco">Discontinuados exitosos <span id="disco-count" style="background:rgba(167,139,250,.2);color:var(--purple);padding:1px 6px;border-radius:3px;font-size:10px;margin-left:4px"></span></button>
  </div>
  <div id="qt-activos">
    <div style="font-size:11px;color:var(--text2);margin-bottom:12px;padding:8px 12px;background:rgba(248,113,113,.05);border:1px solid rgba(248,113,113,.15);border-radius:6px">
      Productos con <strong style="color:var(--red)">stock = 0</strong> que tuvieron ventas. Revenue y unidades del período: <strong id="q-period-label" style="color:var(--text2)">{period_label}</strong>
    </div>
    <div class="filters">
      <select id="qSort" onchange="renderQuiebre()">
        <option value="revenue">Revenue ↓</option>
        <option value="units">Unidades ↓</option>
        <option value="vel_mensual">Velocidad ↓</option>
        <option value="days_wo_stock">Días sin stock ↓</option>
      </select>
    </div>
    <table>
      <thead><tr>
        <th>Producto / SKU</th>
        <th class="tr">Uni. vendidas</th>
        <th class="tr">Revenue</th>
        <th class="tr">Vel/mes</th>
        <th>Rotación</th>
        <th class="tr">Última venta</th>
        <th class="tr">Días sin stock</th>
      </tr></thead>
      <tbody id="q-tbody"></tbody>
    </table>
  </div>
  <div id="qt-disco" style="display:none">
    <div style="font-size:11px;color:var(--text2);margin-bottom:12px;padding:8px 12px;background:rgba(167,139,250,.05);border:1px solid rgba(167,139,250,.2);border-radius:6px">
      Productos con stock=0, sin ventas hace más de 90 días, pero con buena rotación histórica. Candidatos a reponer.
    </div>
    <table>
      <thead><tr>
        <th>Producto / SKU</th>
        <th class="tr">Uni. totales</th>
        <th class="tr">Revenue total</th>
        <th class="tr">Vel/mes histórica</th>
        <th>Rotación</th>
        <th class="tr">Última venta</th>
      </tr></thead>
      <tbody id="disco-tbody"></tbody>
    </table>
  </div>
</div>

<!-- ══════════════════════════════════════════ PLANIFICADOR ══ -->
<div id="page-planificador" class="page">
  <div style="font-size:11px;color:var(--text2);margin-bottom:16px;padding:10px 14px;background:var(--s2);border-radius:6px;border:1px solid var(--border)">
    Velocidad basada en las últimas 4 semanas. Solo productos con ventas en los últimos 90 días. Lead time: 21 días · Safety stock: 14 días.
  </div>

  <!-- Scorecard clickeable -->
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px">
    <div class="plan-cat-card urgente" onclick="setPlanCat('urgente',this)" style="cursor:pointer;background:rgba(248,113,113,.07);border:1px solid rgba(248,113,113,.25);border-radius:10px;padding:14px 16px;border-left:3px solid var(--red);transition:all .15s">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--red);margin-bottom:4px">🔴 Urgente</div>
      <div style="font-family:'Syne',sans-serif;font-size:28px;font-weight:800;color:var(--red);line-height:1" id="plan-urgente">—</div>
      <div style="font-size:10px;color:var(--text3);margin-top:4px">Stock quebrado · buen ritmo</div>
    </div>
    <div class="plan-cat-card critico" onclick="setPlanCat('critico',this)" style="cursor:pointer;background:rgba(251,146,60,.07);border:1px solid rgba(251,146,60,.25);border-radius:10px;padding:14px 16px;border-left:3px solid var(--orange);transition:all .15s">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--orange);margin-bottom:4px">🟠 Crítico</div>
      <div style="font-family:'Syne',sans-serif;font-size:28px;font-weight:800;color:var(--orange);line-height:1" id="plan-critico">—</div>
      <div style="font-size:10px;color:var(--text3);margin-top:4px">Quiebre en &lt;21 días</div>
    </div>
    <div class="plan-cat-card planificar" onclick="setPlanCat('planificar',this)" style="cursor:pointer;background:rgba(240,180,41,.07);border:1px solid rgba(240,180,41,.25);border-radius:10px;padding:14px 16px;border-left:3px solid var(--gold);transition:all .15s">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--gold);margin-bottom:4px">🟡 Planificar</div>
      <div style="font-family:'Syne',sans-serif;font-size:28px;font-weight:800;color:var(--gold);line-height:1" id="plan-planificar">—</div>
      <div style="font-size:10px;color:var(--text3);margin-top:4px">Quiebre en 21–35 días</div>
    </div>
    <div class="plan-cat-card analizar" onclick="setPlanCat('analizar',this)" style="cursor:pointer;background:rgba(96,165,250,.07);border:1px solid rgba(96,165,250,.25);border-radius:10px;padding:14px 16px;border-left:3px solid var(--blue);transition:all .15s">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--blue);margin-bottom:4px">🔵 Analizar</div>
      <div style="font-family:'Syne',sans-serif;font-size:28px;font-weight:800;color:var(--blue);line-height:1" id="plan-analizar">—</div>
      <div style="font-size:10px;color:var(--text3);margin-top:4px">Quiebre en &lt;1 mes · vel. baja</div>
    </div>
  </div>

  <!-- KPI inversión + filtros -->
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
    <div style="background:var(--s2);border:1px solid var(--border);border-radius:8px;padding:10px 16px;display:flex;gap:16px;align-items:center">
      <div>
        <div style="font-size:9px;color:var(--text3);text-transform:uppercase;letter-spacing:1px">Inversión estimada</div>
        <div style="font-family:'Syne',sans-serif;font-size:18px;font-weight:700;color:var(--green)" id="plan-inversion">—</div>
      </div>
      <div style="width:1px;height:30px;background:var(--border)"></div>
      <div>
        <div style="font-size:9px;color:var(--text3);text-transform:uppercase;letter-spacing:1px">Total a reponer</div>
        <div style="font-family:'Syne',sans-serif;font-size:18px;font-weight:700;color:var(--text2)" id="plan-total">—</div>
      </div>
    </div>
    <button class="prio-btn all active" id="planBtnAll" onclick="setPlanCat('',this)" style="border-radius:20px">Todos</button>
    <select id="planSort" onchange="renderPlanificador()" style="margin-left:auto">
      <option value="urgency">Urgencia ↑</option>
      <option value="dias_stock">Días stock ↑</option>
      <option value="vel_mensual">Velocidad ↓</option>
      <option value="revenue">Revenue ↓</option>
      <option value="qty_sugerida">Cant. sugerida ↓</option>
      <option value="inversion">Inversión ↓</option>
    </select>
    <span id="plan-count" style="font-size:11px;color:var(--text3)"></span>
  </div>

  <table>
    <thead><tr>
      <th>Producto / SKU</th>
      <th class="tr">Stock</th>
      <th class="tr">Días rest.</th>
      <th class="tr">Vel/mes</th>
      <th class="tr">Cant. sugerida</th>
      <th class="tr">Inversión est.</th>
      <th>Urgencia</th>
      <th class="tr">Última venta</th>
    </tr></thead>
    <tbody id="plan-tbody"></tbody>
  </table>
</div>

<!-- ══════════════════════════════════════════ BI ══ -->
<div id="page-bi" class="page">
  <div class="g4" style="margin-bottom:20px">
    <div class="kcard"><div class="klabel">Revenue / SKU prom.</div><div class="kvalue" id="bi-rev-sku">—</div></div>
    <div class="kcard"><div class="klabel">SKUs → 80% revenue</div><div class="kvalue b" id="bi-p80">—</div></div>
    <div class="kcard"><div class="klabel">Health Score prom.</div><div class="kvalue g" id="bi-health">—</div></div>
    <div class="kcard"><div class="klabel">En quiebre</div><div class="kvalue r" id="bi-quiebre">—</div></div>
  </div>
  <div class="g2">
    <div class="card"><h3>Top 15 SKUs por revenue <span class="period-badge" id="bi-period">{period_label}</span></h3><canvas id="chartTop15" height="280"></canvas></div>
    <div class="card"><h3>Velocidad de venta — distribución</h3><canvas id="chartVel" height="280"></canvas></div>
  </div>
  <div class="card" style="margin-bottom:20px">
    <h3>Pareto completo — todos los SKUs por revenue <span class="period-badge">{period_label}</span></h3>
    <div style="font-size:11px;color:var(--text3);margin-bottom:10px">Clic en una fila para ver detalle del SKU.</div>
    <table>
      <thead><tr>
        <th>#</th><th>SKU / Producto</th>
        <th class="tr">Revenue</th><th class="tr">% acum.</th>
        <th class="tr">Uni.</th><th class="tr">Vel/mes</th><th>Health</th>
      </tr></thead>
      <tbody id="pareto-tbody"></tbody>
    </table>
    <div class="pag" id="pareto-pag"></div>
  </div>
  <div class="card">
    <h3>Comparación de canales</h3>
    <div id="canal-comparison" style="margin-top:8px"></div>
  </div>
</div>

<!-- ══════════════════════════════════════════ ML PUBLICACIONES ══ -->
<div id="page-ml-pubs" class="page">

  <!-- Tabs por marca -->
  <div style="display:flex;gap:0;margin-bottom:20px;border-bottom:1px solid var(--border);">
    <button class="ml-brand-tab active" id="mltab-all"    onclick="setMLBrand('all',this)"  style="background:none;border:none;border-bottom:2px solid var(--gold);color:var(--gold);padding:8px 20px;cursor:pointer;font-family:inherit;font-size:12px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;">
      Todas
    </button>
    <button class="ml-brand-tab" id="mltab-pret"   onclick="setMLBrand('pret',this)"  style="background:none;border:none;border-bottom:2px solid transparent;color:var(--text2);padding:8px 20px;cursor:pointer;font-family:inherit;font-size:12px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;">
      Pret a Home
    </button>
    <button class="ml-brand-tab" id="mltab-lavan"  onclick="setMLBrand('lavan',this)" style="background:none;border:none;border-bottom:2px solid transparent;color:var(--text2);padding:8px 20px;cursor:pointer;font-family:inherit;font-size:12px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;">
      Casa Lavan
    </button>
    <div style="margin-left:auto;display:flex;align-items:center;padding-bottom:4px;gap:8px">
      <span id="ml-window-label" style="font-size:10px;color:var(--text3)">Últimos 10 días</span>
    </div>
  </div>

  <!-- Scorecard categorías — se actualiza con el tab activo -->
  <div class="ml-scorecard" id="ml-scorecard-grid">
    <div class="ml-cat-card oportunidad" style="cursor:pointer" onclick="setMLCat('oportunidad',null)">
      <div class="ml-cat-label">🟡 Oportunidad</div>
      <div class="ml-cat-count" id="ml-count-oportunidad">—</div>
      <div class="ml-cat-desc">Tráfico pero no convierte</div>
    </div>
    <div class="ml-cat-card invisible" style="cursor:pointer" onclick="setMLCat('invisible',null)">
      <div class="ml-cat-label">🔴 Invisible</div>
      <div class="ml-cat-count" id="ml-count-invisible">—</div>
      <div class="ml-cat-desc">Poco tráfico, no la encuentran</div>
    </div>
    <div class="ml-cat-card muerto" style="cursor:pointer" onclick="setMLCat('muerto',null)">
      <div class="ml-cat-label">⚫ Muerto</div>
      <div class="ml-cat-count" id="ml-count-muerto">—</div>
      <div class="ml-cat-desc">Sin tráfico y sin ventas</div>
    </div>
    <div class="ml-cat-card ganador" style="cursor:pointer" onclick="setMLCat('ganador',null)">
      <div class="ml-cat-label">🟢 Ganador</div>
      <div class="ml-cat-count" id="ml-count-ganador">—</div>
      <div class="ml-cat-desc">Tráfico y conversión buenos</div>
    </div>
  </div>

  <!-- Criterios -->
  <div style="font-size:11px;color:var(--text3);margin-bottom:16px;padding:10px 14px;background:var(--s2);border-radius:6px;border:1px solid var(--border);line-height:1.7">
    <strong style="color:var(--text2)">Criterios — últimos 10 días:</strong>
    ⚫ Muerto: &lt;5 visitas y 0 ventas ·
    🔴 Invisible: &lt;15 visitas ·
    🟡 Oportunidad: ≥15 visitas pero conversión &lt;1% ·
    🟢 Ganador: ≥15 visitas y conversión ≥1%
    <br>
    <strong style="color:var(--gold)">Visitas:</strong>
    Se usan <em>solo</em> si el fetcher guarda <code style="background:var(--s3);padding:1px 4px;border-radius:2px;color:var(--gold)">visits_10d</code> o <code style="background:var(--s3);padding:1px 4px;border-radius:2px;color:var(--gold)">visits_30d</code>.
    Sin ese campo, la columna muestra <strong>—</strong> y la categoría se basa solo en ventas (sin visitas = sin estimación falsa).
    <span id="ml-visitas-warning" style="color:var(--red);font-weight:600;display:none"> ⚠ Sin datos de visitas detectados.</span>
  </div>

  <!-- Filtros -->
  <div class="filters" style="margin-bottom:16px">
    <input style="width:220px" type="text" id="mlSearch" placeholder="🔍 Título o ID..." oninput="filterML()">
    <div class="prio-filter" id="mlCatFilter">
      <button class="prio-btn all active" onclick="setMLCat('',this)">Todas</button>
      <button class="prio-btn oportunidad" onclick="setMLCat('oportunidad',this)">🟡 Oportunidad</button>
      <button class="prio-btn invisible"   onclick="setMLCat('invisible',this)">🔴 Invisible</button>
      <button class="prio-btn muerto"      onclick="setMLCat('muerto',this)">⚫ Muerto</button>
      <button class="prio-btn ganador"     onclick="setMLCat('ganador',this)">🟢 Ganador</button>
    </div>
    <select id="mlSort" onchange="filterML()" style="margin-left:auto">
      <option value="prioridad">Prioridad (urgente primero)</option>
      <option value="visitas_desc">Visitas ↓</option>
      <option value="ventas_desc">Ventas ↓</option>
      <option value="conversion_desc">Conversión ↓</option>
      <option value="revenue_desc">Revenue ↓</option>
    </select>
    <span id="ml-count" style="font-size:11px;color:var(--text3)"></span>
  </div>

  <!-- Tabla -->
  <table id="ml-table">
    <thead><tr>
      <th>Publicación</th>
      <th class="tr">Visitas 10d</th>
      <th class="tr">Ventas 10d</th>
      <th class="tr">Conversión</th>
      <th class="tr">Revenue 10d</th>
      <th>Categoría</th>
    </tr></thead>
    <tbody id="ml-tbody"></tbody>
  </table>
  <div class="pag" id="ml-pag"></div>

  <!-- Cómo agregar visitas -->
  <div style="margin-top:32px;padding:16px 20px;background:var(--s2);border-radius:10px;border:1px solid var(--border)">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--text2);margin-bottom:10px">⚙️ Cómo agregar visitas al fetcher ML</div>
    <div style="font-size:12px;color:var(--text3);line-height:1.9">
      En tu fetcher, para cada item activo, llamá a:
      <br><code style="background:var(--s3);padding:2px 7px;border-radius:3px;color:var(--blue)">GET /items/{{item_id}}/visits?date_greater=YYYY-MM-DD&amp;date_less=YYYY-MM-DD</code>
      <br>Y guardalo en el cache como <code style="background:var(--s3);padding:2px 7px;border-radius:3px;color:var(--gold)">item["visits_10d"] = total_visits</code>
      <br><br>También podés guardar <code style="background:var(--s3);padding:2px 7px;border-radius:3px;color:var(--gold)">last_updated</code> (viene en el objeto item de ML como <code style="background:var(--s3);padding:2px 7px;border-radius:3px;color:var(--text2)">date_last_updated</code>).
    </div>
  </div>
</div>

<!-- DATA -->
<script type="application/json" id="d-products">{products_json}</script>
<script type="application/json" id="d-nosales">{no_sales_json}</script>
<script type="application/json" id="d-quiebre">{quiebre_json}</script>
<script type="application/json" id="d-disco">{disco_json}</script>
<script type="application/json" id="d-purchase">{purchase_json}</script>
<script type="application/json" id="d-monthly">{monthly_json}</script>
<script type="application/json" id="d-canal">{canal_totals_json}</script>
<script type="application/json" id="d-clabels">{canal_labels_json}</script>
<script type="application/json" id="d-months">{months_keys_json}</script>
<script type="application/json" id="d-mlpubs">{ml_pubs_json}</script>
<script type="application/json" id="d-daily">{daily_totals_json}</script>

<script>
const PERIOD_LABEL = "{period_label}";
const DATE_FROM    = "{date_from}";
const DATE_TO      = "{date_to}";
</script>
<script id="main-js">
</script>

</body>
</html>"""

    # JS principal — fuera del f-string para evitar conflictos de escape
    main_js = r"""
// DATA
const ALL_PRODUCTS   = JSON.parse(document.getElementById('d-products').textContent);
const NO_SALES_DATA  = JSON.parse(document.getElementById('d-nosales').textContent);
const QUIEBRE_DATA   = JSON.parse(document.getElementById('d-quiebre').textContent);
const DISCO_DATA     = JSON.parse(document.getElementById('d-disco').textContent);
const PURCHASE_PLAN  = JSON.parse(document.getElementById('d-purchase').textContent);
const MONTHLY_DATA   = JSON.parse(document.getElementById('d-monthly').textContent);
const CANAL_TOTALS   = JSON.parse(document.getElementById('d-canal').textContent);
const CANAL_LABELS   = JSON.parse(document.getElementById('d-clabels').textContent);
const MONTHS_KEYS    = JSON.parse(document.getElementById('d-months').textContent);
const ML_PUBS        = JSON.parse(document.getElementById('d-mlpubs').textContent);
const DAILY_DATA     = JSON.parse(document.getElementById('d-daily').textContent);
// PERIOD_LABEL, DATE_FROM, DATE_TO inyectados arriba

// STATE
let filteredProducts = [...ALL_PRODUCTS];
let filteredNoSales  = [...NO_SALES_DATA];
let filteredPareto   = [...ALL_PRODUCTS];
let filteredML       = [...ML_PUBS];
let prodPage=1, nsPage=1, paretoPage=1, mlPage=1;
const PAGE = 40;
let mlCatActive = '';

// DRAWER
function toggleDrawer() {
  const drawer  = document.getElementById('drawer');
  const overlay = document.getElementById('drawerOverlay');
  const btn     = document.getElementById('hamburgerBtn');
  const isOpen  = drawer.classList.contains('open');
  drawer.classList.toggle('open', !isOpen);
  overlay.classList.toggle('open', !isOpen);
  btn.classList.toggle('open', !isOpen);
}
function closeDrawer() {
  document.getElementById('drawer').classList.remove('open');
  document.getElementById('drawerOverlay').classList.remove('open');
  document.getElementById('hamburgerBtn').classList.remove('open');
}
document.addEventListener('keydown', e => { if(e.key==='Escape') closeDrawer(); });

// FORMATTERS
const fmt  = n => '$' + Math.round(n||0).toLocaleString('es-AR');
const fmtN = n => (n||0).toLocaleString('es-AR');
const fmtD = n => n!==null&&n!==undefined ? n+'d' : '--';
const fmtPct = n => (n||0).toFixed(1) + '%';

function daysColor(d){ if(d===0||d===null)return 'var(--red)';if(d<14)return 'var(--orange)';if(d<30)return 'var(--gold)';return 'var(--text2)'; }
function planDaysColor(d){ if(d<=0)return 'var(--red)';if(d<21)return 'var(--orange)';return 'var(--gold)'; }
function cumColor(c){ if(c<50)return 'var(--green)';if(c<80)return 'var(--gold)';return 'var(--text2)'; }
function stockValColor(s){ return s===0?'var(--red)':'inherit'; }
function hClass(h){ if(h>=75)return 'he';if(h>=50)return 'hg';if(h>=25)return 'hf';return 'hp'; }

function stockBadge(p) {
  if(p.stock_status==='quiebre') return '<span class="badge br">Sin stock</span>';
  if(p.stock_status==='critico') return '<span class="badge bo">Critico</span>';
  if(p.stock_status==='bajo')    return '<span class="badge by">Bajo</span>';
  if(p.stock_status==='ok')      return '<span class="badge bg">OK</span>';
  return '<span class="badge bgr">-</span>';
}

function canalPills(by_canal) {
  return Object.keys(by_canal||{}).map(c => {
    const cls = c.startsWith('ml') ? 'cml' : 'ctn';
    return '<span class="cpill ' + cls + '">' + (CANAL_LABELS[c]||c) + '</span>';
  }).join('');
}

function urgBadge(u) {
  const m = {
    urgente:   '<span class="badge br">URGENTE</span>',
    critico:   '<span class="badge bo">CRITICO</span>',
    planificar:'<span class="badge by">PLANIFICAR</span>',
    analizar:  '<span class="badge bb">ANALIZAR</span>',
  };
  return m[u]||'';
}

function rotBadge(r) {
  if(r==='alta')  return '<span class="badge br">ALTA</span>';
  if(r==='media') return '<span class="badge by">MEDIA</span>';
  return '<span class="badge bgr">BAJA</span>';
}

// CHARTS
Chart.defaults.color='#5a5c64';
Chart.defaults.borderColor='#2a2b2f';
Chart.defaults.font.family='Inter';
Chart.defaults.font.size=11;
let charts={};

function initBICharts() {
  if(charts.top15)charts.top15.destroy();
  if(charts.vel)charts.vel.destroy();
  const t15=ALL_PRODUCTS.slice(0,15);
  charts.top15=new Chart(document.getElementById('chartTop15'),{
    type:'bar',indexAxis:'y',
    data:{labels:t15.map(p=>p.title.slice(0,32)+'...'),
      datasets:[{data:t15.map(p=>p.revenue),backgroundColor:'#f0b42960',borderColor:'#f0b429',borderWidth:1,borderRadius:3}]
    },
    options:{responsive:true,plugins:{legend:{display:false}},
      scales:{x:{ticks:{callback:v=>'$'+(v/1000).toFixed(0)+'k'}}}
    }
  });
  const vb=[0,0,0,0,0];
  ALL_PRODUCTS.forEach(p=>{const v=p.vel_mensual||0;if(v===0)vb[0]++;else if(v<0.5)vb[1]++;else if(v<1)vb[2]++;else if(v<3)vb[3]++;else vb[4]++;});
  charts.vel=new Chart(document.getElementById('chartVel'),{
    type:'bar',
    data:{labels:['0/mes','<0.5','0.5-1','1-3','>3/mes'],
      datasets:[{data:vb,backgroundColor:'#60a5fa40',borderColor:'#60a5fa',borderWidth:1,borderRadius:4}]
    },
    options:{responsive:true,plugins:{legend:{display:false}}}
  });
}

// OVERVIEW
function renderOverview() {
  const gf = G;
  const _d=gf.desde, _h=gf.hasta, _c=gf.canal, _m=gf.marca;
  const now = new Date();
  const last30from = new Date(now-30*864e5).toISOString().slice(0,10);
  const isCustom = _d!==DATE_FROM||_h!==DATE_TO||_c||_m;
  const useDesde = isCustom ? _d : last30from;
  const useHasta = isCustom ? _h : now.toISOString().slice(0,10);
  let rev=0,units=0,skuCount=0;
  const allFiltered = ALL_PRODUCTS.filter(p=>matchesCanalMarca(p,_c,_m));
  allFiltered.forEach(p=>{ const rr=calcPeriod(p,useDesde,useHasta,_c); rev+=rr.rev; units+=rr.units; if(rr.rev>0)skuCount++; });
  const ticket = skuCount>0?rev/skuCount:0;
  const margins=allFiltered.filter(p=>p.margen_pct!==null).map(p=>p.margen_pct);
  const avgM=margins.length?margins.reduce((a,b)=>a+b)/margins.length:0;
  const periodLabel=isCustom?_d+' -> '+_h:'ultimos 30d';
  document.getElementById('ov-revenue').textContent=fmt(rev);
  document.getElementById('ov-units').textContent=fmtN(units);
  document.getElementById('ov-ticket').textContent=fmt(ticket);
  document.getElementById('ov-margen').textContent=avgM.toFixed(1)+'%';
  document.getElementById('ov-skus').textContent=allFiltered.length;
  document.getElementById('ov-sinventas').textContent='sin ventas: '+NO_SALES_DATA.length;
  document.querySelectorAll('.kcard .period-badge').forEach(el=>el.textContent=periodLabel);

  // Pareto
  const sorted=allFiltered.map(p=>{ const rr=calcPeriod(p,_d,_h,_c); const obj=Object.assign({},p); obj._rev=rr.rev; return obj; }).sort((a,b)=>b._rev-a._rev);
  const totalRev=sorted.reduce((s,p)=>s+p._rev,0);
  const n20=Math.max(1,Math.floor(sorted.length/5));
  const r20=sorted.slice(0,n20).reduce((s,p)=>s+p._rev,0);
  const pct=totalRev>0?r20/totalRev*100:0;
  document.getElementById('pareto-pct').textContent=pct.toFixed(0)+'%';
  document.getElementById('pareto-desc').textContent='del top 20% de SKUs ('+n20+') genera del revenue total';
  document.getElementById('pb-fill').style.width=pct.toFixed(0)+'%';
  document.getElementById('top5-list').innerHTML=sorted.slice(0,5).map(function(p,i){
    return '<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border);font-size:11px;cursor:pointer" data-sku="'+p.sku+'">'
      +'<span style="color:var(--text2)">'+(i+1)+'. '+p.title.slice(0,28)+'...</span>'
      +'<span style="color:var(--gold)">'+fmt(p._rev)+'</span>'
      +'</div>';
  }).join('');

  // Estado stock
  const sq=allFiltered.filter(p=>p.stock_status==='quiebre').length;
  const sc=allFiltered.filter(p=>p.stock_status==='critico').length;
  const sb=allFiltered.filter(p=>p.stock_status==='bajo').length;
  const so=allFiltered.filter(p=>p.stock_status==='ok').length;
  const stockItems=[[sq,'Quiebre','var(--red)','quiebre'],[sc,'Critico','var(--orange)','critico'],[sb,'Bajo','var(--gold)','bajo'],[so,'OK','var(--green)','ok']];
  document.getElementById('stock-summary').innerHTML=stockItems.map(function(item){
    return '<div style="text-align:center;padding:10px;background:var(--s3);border-radius:6px;cursor:pointer" onclick="filterByStock(\''+item[3]+'\')">'
      +'<div style="font-size:22px;font-family:Syne,sans-serif;font-weight:700;color:'+item[2]+'">'+item[0]+'</div>'
      +'<div style="font-size:10px;color:var(--text2);margin-top:2px">'+item[1]+'</div>'
      +'</div>';
  }).join('');

  // Health Score de Stock
  const stockProds=allFiltered.filter(p=>p.stock!==null&&p.stock>0);
  const totalStock=stockProds.length||1;
  const shCats={critico:[],muyMalo:[],malo:[],regular:[],bueno:[],excelente:[]};
  const now30=new Date(now-30*864e5).toISOString().slice(0,10);
  stockProds.forEach(p=>{
    const sinMov=!p.last_sale||p.last_sale<now30;
    const stk=p.stock||0;
    const ds=p.dias_stock;
    if(sinMov){
      if(stk>50)shCats.critico.push(p);
      else if(stk>=30)shCats.muyMalo.push(p);
      else shCats.malo.push(p);
    } else {
      if(ds!==null&&ds<15)shCats.excelente.push(p);
      else if(ds!==null&&ds<30)shCats.bueno.push(p);
      else shCats.regular.push(p);
    }
  });
  const shDefs=[
    {key:'critico',  label:'Critico',   desc:'+30d sin venta >50u', color:'var(--red)',   bg:'rgba(248,113,113,.12)'},
    {key:'muyMalo',  label:'Muy malo',  desc:'+30d sin venta 30-50u',color:'var(--orange)',bg:'rgba(251,146,60,.1)'},
    {key:'malo',     label:'Malo',      desc:'+30d sin venta <30u',  color:'var(--gold)',  bg:'rgba(240,180,41,.1)'},
    {key:'regular',  label:'Regular',   desc:'Con ventas, quiebre >30d',color:'var(--text2)',bg:'rgba(138,140,148,.1)'},
    {key:'bueno',    label:'Bueno',     desc:'Quiebre 15-30d',       color:'var(--green)', bg:'rgba(52,211,153,.1)'},
    {key:'excelente',label:'Excelente', desc:'Quiebre <15d',         color:'#a78bfa',      bg:'rgba(167,139,250,.1)'},
  ];
  document.getElementById('stock-health-bar').innerHTML=shDefs.map(function(d){
    const nn=shCats[d.key].length;
    const pp=nn/totalStock*100;
    if(pp<0.5)return '';
    return '<div title="'+d.label+': '+nn+' SKUs ('+pp.toFixed(1)+'%)" style="width:'+pp.toFixed(1)+'%;background:'+d.color+';opacity:.7;min-width:3px"></div>';
  }).join('');
  document.getElementById('stock-health-grid').innerHTML=shDefs.map(function(d){
    const nn=shCats[d.key].length;
    const pp=totalStock>0?(nn/totalStock*100).toFixed(1):'0';
    return '<div style="background:'+d.bg+';border-radius:8px;padding:12px;border:1px solid rgba(128,128,128,0.2);cursor:pointer" onclick="filterStockHealth(\''+d.key+'\')">'
      +'<div style="font-size:11px;font-weight:700;color:'+d.color+';margin-bottom:2px">'+d.label+'</div>'
      +'<div style="font-family:Syne,sans-serif;font-size:22px;font-weight:800;color:'+d.color+'">'+nn+'</div>'
      +'<div style="font-size:10px;color:var(--text3);margin-top:2px">'+pp+'% - '+d.desc+'</div>'
      +'</div>';
  }).join('');
}

function filterStockHealth(cat) {
  showPage('productos', document.querySelector('[data-page=productos]'));
  if(cat==='critico'||cat==='muyMalo'||cat==='malo') {
    document.getElementById('pActF').value='inactivo';
  } else {
    document.getElementById('pActF').value='activo';
  }
  if(cat==='excelente'||cat==='bueno') {
    document.getElementById('pStockF').value='ok';
  } else {
    document.getElementById('pStockF').value='';
  }
  filterProducts();
}

function filterByStock(s) {
  document.getElementById('pStockF').value=s;
  filterProducts();
  showPage('productos',document.querySelector('[data-page=productos]'));
}

function initOverviewCharts() {
  if(charts.revenue)charts.revenue.destroy();
  if(charts.canal)charts.canal.destroy();
  if(charts.c10d)charts.c10d.destroy();

  const cColors={'ml_pret':'#f0b429','tn_pret':'#60a5fa','tn_lavan':'#a78bfa','ml_lavan':'#34d399'};
  const cKeys=Object.keys(CANAL_LABELS);

  // Chart 10 dias — datos reales de DAILY_DATA
  const now10=new Date();
  const days10=[], days10Labels=[];
  for(let i=9;i>=0;i--){
    const d=new Date(now10-i*864e5);
    days10.push(d.toISOString().slice(0,10));
    days10Labels.push(d.toLocaleDateString('es-AR',{weekday:'short',day:'numeric',month:'short'}));
  }
  const el10d=document.getElementById('ov-10d-range');
  if(el10d)el10d.textContent=days10[0]+' -> '+days10[days10.length-1];

  charts.c10d=new Chart(document.getElementById('chart10d'),{
    type:'line',
    data:{
      labels:days10Labels,
      datasets:cKeys.map(k=>({
        label:CANAL_LABELS[k]||k,
        data:days10.map(function(d){
          return DAILY_DATA[d]&&DAILY_DATA[d][k]?Math.round(DAILY_DATA[d][k].revenue):0;
        }),
        borderColor:cColors[k]||'#8a8c94',
        backgroundColor:(cColors[k]||'#8a8c94')+'18',
        fill:true,tension:0.4,pointRadius:4,pointHoverRadius:7,borderWidth:2,
      }))
    },
    options:{
      responsive:true,
      interaction:{mode:'index',intersect:false},
      plugins:{
        legend:{position:'bottom',labels:{boxWidth:8,padding:12}},
        tooltip:{callbacks:{label:function(ctx){
          const k=cKeys[ctx.datasetIndex];
          const d=days10[ctx.dataIndex];
          const units=DAILY_DATA[d]&&DAILY_DATA[d][k]?DAILY_DATA[d][k].units:0;
          return ' '+(CANAL_LABELS[k]||k)+': $'+ctx.raw.toLocaleString('es-AR')+' / '+units+' ventas';
        }}}
      },
      scales:{
        x:{grid:{color:'#2a2b2f'},ticks:{font:{size:10}}},
        y:{grid:{color:'#2a2b2f'},ticks:{callback:function(v){return '$'+(v/1000).toFixed(0)+'k';}}}
      }
    }
  });

  charts.revenue=new Chart(document.getElementById('chartRevenue'),{
    type:'line',
    data:{labels:MONTHS_KEYS,datasets:cKeys.map(k=>({
      label:CANAL_LABELS[k]||k,
      data:MONTHS_KEYS.map(m=>MONTHLY_DATA[m]&&MONTHLY_DATA[m][k]?MONTHLY_DATA[m][k].revenue:0),
      borderColor:cColors[k]||'#8a8c94',
      backgroundColor:(cColors[k]||'#8a8c94')+'15',
      fill:false,tension:0.4,pointRadius:3
    }))},
    options:{responsive:true,plugins:{legend:{position:'bottom',labels:{boxWidth:8,padding:10}}},
      scales:{y:{ticks:{callback:v=>'$'+(v/1000).toFixed(0)+'k'}}}
    }
  });

  const cRevs=cKeys.map(k=>Object.values(MONTHLY_DATA).reduce((s,m)=>s+(m[k]?m[k].revenue:0),0));
  charts.canal=new Chart(document.getElementById('chartCanal'),{
    type:'doughnut',
    data:{labels:cKeys.map(k=>CANAL_LABELS[k]||k),datasets:[{
      data:cRevs,
      backgroundColor:['#f0b42940','#60a5fa40','#a78bfa40','#34d39940'],
      borderColor:['#f0b429','#60a5fa','#a78bfa','#34d399'],borderWidth:1.5
    }]},
    options:{responsive:true,plugins:{legend:{position:'bottom',labels:{boxWidth:8,padding:8}}}}
  });
}

// PRODUCTOS
let prodSortField='revenue', prodSortDir=-1;
function sortProds(field){ if(prodSortField===field)prodSortDir*=-1;else{prodSortField=field;prodSortDir=-1;} filterProducts(); }

function filterProducts() {
  const q=document.getElementById('pSearch').value.toLowerCase();
  const sort=document.getElementById('pSort').value;
  const stockF=document.getElementById('pStockF').value;
  const actF=document.getElementById('pActF').value;
  const minR=parseFloat(document.getElementById('pMinRev').value)||0;
  const gf2=G; const _d=gf2.desde,_h=gf2.hasta,_c=gf2.canal,_m=gf2.marca;
  const working=ALL_PRODUCTS.filter(p=>matchesCanalMarca(p,_c,_m)).map(function(p){
    const rr=calcPeriod(p,_d,_h,_c);
    const activity=getActivity(p.last_sale);
    const obj=Object.assign({},p);
    obj._rev=rr.rev; obj._units=rr.units; obj._orders=rr.orders; obj._activity=activity;
    return obj;
  }).filter(function(p){
    if(q&&!p.title.toLowerCase().includes(q)&&!p.sku.toLowerCase().includes(q))return false;
    if(stockF&&p.stock_status!==stockF)return false;
    if(actF&&p._activity!==actF)return false;
    if(minR&&p._rev<minR)return false;
    if(p._rev===0&&p._units===0&&(_d!==DATE_FROM||_h!==DATE_TO))return false;
    return true;
  });
  const sf=prodSortField||sort;
  working.sort(function(a,b){
    let av=sf==='revenue'?a._rev:sf==='units'?a._units:sf==='last_sale'?(a[sf]||''):(a[sf]||0);
    let bv=sf==='revenue'?b._rev:sf==='units'?b._units:sf==='last_sale'?(b[sf]||''):(b[sf]||0);
    if(sf==='last_sale')return bv.localeCompare(av);
    return((bv-av)*prodSortDir*-1)||0;
  });
  filteredProducts=working;prodPage=1;renderProducts();
}

function clearProdFilters(){
  ['pSearch','pMinRev'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('pSort').value='revenue';
  document.getElementById('pStockF').value='';
  document.getElementById('pActF').value='';
  filterProducts();
}

function renderProducts(){
  const start=(prodPage-1)*PAGE;
  const page=filteredProducts.slice(start,start+PAGE);
  const maxR=filteredProducts[0]?filteredProducts[0]._rev||filteredProducts[0].revenue||1:1;
  document.getElementById('p-count').textContent=filteredProducts.length+' SKUs';
  document.getElementById('prod-tbody').innerHTML=page.map(function(p,i){
    return '<tr data-sku="'+p.sku+'">'
      +'<td style="color:var(--text3);width:28px">'+(start+i+1)+'</td>'
      +'<td><div class="pcell">'
      +(p.thumbnail?'<img class="pthumb" src="'+p.thumbnail+'" loading="lazy">':'<div class="pthumb-e"></div>')
      +'<div style="min-width:0">'
      +'<div class="ptitle" title="'+p.title+'">'+p.title+'</div>'
      +'<div class="psku">'+p.sku+'</div>'
      +'<div class="rbar"><div class="rfill" style="width:'+Math.round(((p._rev||p.revenue)/maxR)*100)+'%"></div></div>'
      +'</div></div></td>'
      +'<td><div class="hring '+hClass(p.health)+'">'+Math.round(p.health)+'</div></td>'
      +'<td class="tra">'+fmt(p._rev!=null?p._rev:p.revenue)+'</td>'
      +'<td class="tr">'+fmtN(p._units!=null?p._units:p.units)+'</td>'
      +'<td class="tr">'+p.vel_mensual.toFixed(1)+'/mes</td>'
      +'<td class="tr">'+(p.margen_pct!==null?p.margen_pct.toFixed(1)+'%':'--')+'</td>'
      +'<td class="tr">'+stockBadge(p)+' '+(p.stock!==null?p.stock:'--')+'</td>'
      +'<td class="tr" style="color:'+daysColor(p.dias_stock)+'">'+fmtD(p.dias_stock)+'</td>'
      +'<td class="tr" style="color:var(--text3)">'+p.last_sale+'</td>'
      +'<td>'+canalPills(p.by_canal)+'</td>'
      +'</tr>';
  }).join('');
  renderPag('prod-pag',filteredProducts.length,prodPage,function(n){prodPage=n;renderProducts();});
}

// Event delegation for SKU row clicks
document.addEventListener('click', function(e) {
  const row=e.target.closest('[data-sku]');
  if(row&&!e.target.closest('#ml-tbody'))openSKU(row.dataset.sku);
});

let prevPage='overview';
let skuChart1=null, skuChart2=null;

// SKU DETAIL
function openSKU(sku){
  const p=ALL_PRODUCTS.find(x=>x.sku===sku);
  if(!p)return;
  prevPage=document.querySelector('.drawer-item.active')?document.querySelector('.drawer-item.active').dataset.page:'productos';
  showPage('sku-detail',document.getElementById('tab-sku-detail'));
  document.getElementById('sku-empty-state').style.display='none';
  document.getElementById('sku-detail-content').style.display='block';
  document.getElementById('sku-back-btn').style.display='';
  document.getElementById('skuSearch').value=p.title+' ('+p.sku+')';
  document.getElementById('sku-header-content').innerHTML=
    '<div class="sku-header">'
    +(p.thumbnail?'<img class="sku-thumb" src="'+p.thumbnail+'">':'<div class="sku-thumb-e"></div>')
    +'<div class="sku-info" style="flex:1">'
    +'<h2>'+p.title+'</h2>'
    +'<div class="sku-code">SKU: '+p.sku+'</div>'
    +'<div class="sku-links" id="sku-links-container"></div>'
    +'</div>'
    +'<div style="text-align:right">'
    +'<div class="hring '+hClass(p.health)+'" style="width:48px;height:48px;font-size:16px">'+Math.round(p.health)+'</div>'
    +'<div style="font-size:10px;color:var(--text3);margin-top:4px">Health Score</div>'
    +'</div></div>';
  const linksEl=document.getElementById('sku-links-container');
  Object.entries(p.by_canal||{}).forEach(function(entry){
    const c=entry[0], v=entry[1];
    const isML=c.startsWith('ml');
    (v.item_ids||[]).forEach(function(id){
      const lbl=isML?'ML: '+id:'TN: '+id;
      const href=isML?'https://www.mercadolibre.com.ar/p/'+id:'#';
      const cls=isML?'ml':'tn';
      linksEl.innerHTML+='<a href="'+href+'" target="_blank" class="sku-link '+cls+'">'+lbl+' &#8599;</a>';
    });
    if(p.permalink&&isML)linksEl.innerHTML+='<a href="'+p.permalink+'" target="_blank" class="sku-link ml">Ver en ML &#8599;</a>';
  });
  document.getElementById('sku-kpis').innerHTML=
    '<div class="kcard"><div class="klabel">Revenue total</div><div class="kvalue">'+fmt(p.revenue)+'</div></div>'
    +'<div class="kcard"><div class="klabel">Unidades</div><div class="kvalue b">'+fmtN(p.units)+'</div></div>'
    +'<div class="kcard"><div class="klabel">Velocidad</div><div class="kvalue">'+p.vel_mensual.toFixed(1)+'</div><div class="ksub">uni/mes</div></div>'
    +'<div class="kcard"><div class="klabel">Stock actual</div><div class="kvalue '+(p.stock===0?'r':'g')+'">'+  (p.stock!==null?p.stock:'--')+'</div><div class="ksub">'+stockBadge(p)+'</div></div>';
  const months=Object.keys(p.by_month||{}).sort();
  if(skuChart1)skuChart1.destroy();
  skuChart1=new Chart(document.getElementById('skuChartMonthly'),{
    type:'bar',
    data:{labels:months,datasets:[
      {label:'Revenue',data:months.map(m=>p.by_month[m].revenue),backgroundColor:'#f0b42950',borderColor:'#f0b429',borderWidth:1,borderRadius:3,yAxisID:'y'},
      {label:'Uni.',data:months.map(m=>p.by_month[m].units),backgroundColor:'#60a5fa50',borderColor:'#60a5fa',borderWidth:1,borderRadius:3,yAxisID:'y1',type:'line'}
    ]},
    options:{responsive:true,plugins:{legend:{position:'bottom',labels:{boxWidth:8}}},
      scales:{y:{ticks:{callback:v=>'$'+(v/1000).toFixed(0)+'k'}},y1:{position:'right',grid:{drawOnChartArea:false}}}
    }
  });
  const cKeys2=Object.keys(p.by_canal||{});
  if(skuChart2)skuChart2.destroy();
  skuChart2=new Chart(document.getElementById('skuChartCanal'),{
    type:'doughnut',
    data:{labels:cKeys2.map(k=>CANAL_LABELS[k]||k),datasets:[{
      data:cKeys2.map(k=>p.by_canal[k].revenue),
      backgroundColor:['#f0b42940','#60a5fa40','#a78bfa40'],
      borderColor:['#f0b429','#60a5fa','#a78bfa'],borderWidth:1.5
    }]},
    options:{responsive:true,plugins:{legend:{position:'bottom'}}}
  });
  document.getElementById('sku-info-table').innerHTML='<table><tbody>'
    +[['Precio','$'+Math.round(p.price).toLocaleString('es-AR')],
      ['Costo',p.cost>0?'$'+Math.round(p.cost).toLocaleString('es-AR'):'--'],
      ['Margen',p.margen_pct!==null?p.margen_pct.toFixed(1)+'%':'--'],
      ['Dias stock',fmtD(p.dias_stock)],
      ['Primera venta',p.first_sale||'--'],
      ['Ultima venta',p.last_sale||'--'],
      ['Ordenes totales',p.orders],
    ].map(function(row){return '<tr style="border-bottom:1px solid var(--border)"><td style="padding:6px 8px;color:var(--text3)">'+row[0]+'</td><td style="padding:6px 8px;font-weight:500">'+row[1]+'</td></tr>';}).join('')
    +'</tbody></table>';
  document.getElementById('sku-publications').innerHTML='<div style="font-size:11px;color:var(--text3);margin-bottom:8px">Canales con ventas en el periodo:</div>'
    +Object.entries(p.by_canal||{}).map(function(entry){
      const c=entry[0], v=entry[1];
      return '<div style="display:flex;justify-content:space-between;padding:8px;background:var(--s3);border-radius:6px;margin-bottom:6px">'
        +'<span style="font-weight:500">'+(CANAL_LABELS[c]||c)+'</span>'
        +'<span>'+fmt(v.revenue)+' - '+fmtN(v.units)+' uni.</span>'
        +'</div>';
    }).join('');
}

function goBack(){
  document.getElementById('sku-detail-content').style.display='none';
  document.getElementById('sku-empty-state').style.display='block';
  document.getElementById('sku-back-btn').style.display='none';
  document.getElementById('skuSearch').value='';
  document.getElementById('skuSuggestions').style.display='none';
  const target=document.querySelector('[data-page="'+prevPage+'"]');
  showPage(prevPage,target);
}

function searchSKU(q){
  const sug=document.getElementById('skuSuggestions');
  if(!q||q.length<2){sug.style.display='none';return;}
  const ql=q.toLowerCase();
  const matches=ALL_PRODUCTS.filter(p=>p.sku.toLowerCase().includes(ql)||p.title.toLowerCase().includes(ql)).slice(0,10);
  if(!matches.length){sug.style.display='none';return;}
  sug.innerHTML=matches.map(function(p){
    return '<div style="padding:10px 14px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;gap:10px;align-items:center" onmousedown="openSKU(\''+p.sku+'\')" onmouseover="this.style.background=\'var(--s3)\'" onmouseout="this.style.background=\'\'">'
      +(p.thumbnail?'<img src="'+p.thumbnail+'" style="width:32px;height:32px;object-fit:cover;border-radius:4px">':'<div style="width:32px;height:32px;background:var(--s3);border-radius:4px"></div>')
      +'<div><div style="font-size:12px;font-weight:500;color:var(--text)">'+p.title.slice(0,50)+'</div>'
      +'<div style="font-size:10px;color:var(--text3)">'+p.sku+' - '+fmt(p.revenue)+' - Health: '+Math.round(p.health)+'</div></div>'
      +'</div>';
  }).join('');
  sug.style.display='block';
}
document.addEventListener('click',function(e){
  if(!e.target.closest('#skuSearch')&&!e.target.closest('#skuSuggestions'))
    document.getElementById('skuSuggestions').style.display='none';
});

// SIN VENTAS
function filterNoSales(){
  const q=document.getElementById('nsSearch').value.toLowerCase();
  const stockF=document.getElementById('nsStockF').value;
  const ageF=parseInt(document.getElementById('nsAge').value);
  const sortF=document.getElementById('nsSort').value;
  const today=new Date();
  filteredNoSales=NO_SALES_DATA.filter(function(p){
    if(q&&!p.title.toLowerCase().includes(q)&&!(p.sku||'').toLowerCase().includes(q))return false;
    if(stockF==='con'&&!(p.stock>0))return false;
    if(stockF==='sin'&&p.stock!==0)return false;
    if(ageF>0){if(!p.created_at)return true;const d=Math.floor((today-new Date(p.created_at))/864e5);if(d<ageF)return false;}
    return true;
  });
  filteredNoSales.sort(function(a,b){
    if(sortF==='stock_desc')return(b.stock||0)-(a.stock||0);
    if(sortF==='price_desc')return(b.avg_price||b.price||0)-(a.avg_price||a.price||0);
    if(sortF==='age_desc')return(a.created_at||'').localeCompare(b.created_at||'');
    if(sortF==='notselling'){const da=a.created_at?Math.floor((today-new Date(a.created_at))/864e5):0;const db=b.created_at?Math.floor((today-new Date(b.created_at))/864e5):0;return db-da;}
    return(a.title||'').localeCompare(b.title||'');
  });
  nsPage=1;renderNoSales();
}

function renderNoSales(){
  const start=(nsPage-1)*PAGE;
  const page=filteredNoSales.slice(start,start+PAGE);
  const today=new Date();
  document.getElementById('ns-count').textContent=filteredNoSales.length+' publicaciones';
  document.getElementById('ns-grid').innerHTML=page.map(function(p){
    const diasPub=p.created_at?Math.floor((today-new Date(p.created_at))/864e5):null;
    const stockB=p.stock>0?'<span class="badge bg">Stock: '+p.stock+'</span>':'<span class="badge br">Sin stock</span>';
    let pubB='';
    if(diasPub!==null){
      let cls,label;
      if(diasPub<7){cls='bb';label='Pub hace '+diasPub+'d';}
      else if(diasPub<30){cls='bb';label='Pub hace '+diasPub+'d';}
      else if(diasPub<60){cls='by';label='Pub hace '+diasPub+'d';}
      else if(diasPub<90){cls='bo';label='Sin ventas '+diasPub+'d';}
      else{cls='br';label='Sin ventas '+diasPub+'d';}
      pubB='<span class="badge '+cls+'">'+label+'</span>';
    }
    return '<div class="ns-card">'
      +(p.thumbnail?'<img src="'+p.thumbnail+'" loading="lazy">':'<div class="ns-img"></div>')
      +'<div class="ns-info" style="flex:1;min-width:0">'
      +'<div class="nst">'+(p.permalink?'<a href="'+p.permalink+'" target="_blank">'+p.title+'</a>':p.title)+'</div>'
      +'<div class="nsm">'+fmt(p.avg_price||p.price||0)+' - '+(p.sku||p.item_id||'--')+'</div>'
      +(p.created_at?'<div class="nsm" style="color:var(--text3)">Pub: '+p.created_at.slice(0,10)+'</div>':'')
      +'<div style="margin-top:5px;display:flex;gap:4px;flex-wrap:wrap">'+stockB+' '+pubB+'</div>'
      +'</div></div>';
  }).join('')||'<p style="color:var(--text3);padding:20px">Sin publicaciones sin ventas</p>';
  renderPag('ns-pag',filteredNoSales.length,nsPage,function(n){nsPage=n;renderNoSales();});
}

// QUIEBRE
function renderQuiebre(){
  const sort=document.getElementById('qSort').value;
  const sorted=[...QUIEBRE_DATA].sort(function(a,b){
    if(sort==='days_wo_stock'){const da=a.last_sale?Math.floor((new Date()-new Date(a.last_sale))/864e5):0;const db=b.last_sale?Math.floor((new Date()-new Date(b.last_sale))/864e5):0;return db-da;}
    return(b[sort]||0)-(a[sort]||0);
  });
  document.getElementById('q-tbody').innerHTML=sorted.map(function(p){
    const dws=p.last_sale?Math.floor((new Date()-new Date(p.last_sale))/864e5):null;
    return '<tr data-sku="'+p.sku+'">'
      +'<td><div class="pcell">'+(p.thumbnail?'<img class="pthumb" src="'+p.thumbnail+'" loading="lazy">':'<div class="pthumb-e"></div>')+'<div><div class="ptitle">'+p.title+'</div><div class="psku">'+p.sku+'</div></div></div></td>'
      +'<td class="tr">'+fmtN(p.units)+'</td><td class="tra">'+fmt(p.revenue)+'</td>'
      +'<td class="tr">'+p.vel_mensual.toFixed(1)+'/mes</td>'
      +'<td>'+rotBadge(p.rot_label)+'</td>'
      +'<td class="tr" style="color:var(--text3)">'+p.last_sale+'</td>'
      +'<td class="tr" style="color:var(--red)">'+fmtD(dws)+'</td>'
      +'</tr>';
  }).join('')||'<tr><td colspan="7" style="padding:20px;color:var(--text3)">Sin quiebres</td></tr>';
  document.getElementById('disco-tbody').innerHTML=DISCO_DATA.map(function(p){
    return '<tr data-sku="'+p.sku+'">'
      +'<td><div class="pcell">'+(p.thumbnail?'<img class="pthumb" src="'+p.thumbnail+'" loading="lazy">':'<div class="pthumb-e"></div>')+'<div><div class="ptitle">'+p.title+'</div><div class="psku">'+p.sku+'</div></div></div></td>'
      +'<td class="tr">'+fmtN(p.units)+'</td><td class="tra">'+fmt(p.revenue)+'</td>'
      +'<td class="tr">'+p.vel_mensual.toFixed(1)+'/mes</td>'
      +'<td>'+rotBadge(p.rot_label)+'</td>'
      +'<td class="tr" style="color:var(--text3)">'+p.last_sale+'</td>'
      +'</tr>';
  }).join('')||'<tr><td colspan="6" style="padding:20px;color:var(--text3)">Sin discontinuados</td></tr>';
  document.getElementById('disco-count').textContent=DISCO_DATA.length;
}

function switchQTab(tab,btn){
  document.getElementById('qt-activos').style.display=tab==='activos'?'':'none';
  document.getElementById('qt-disco').style.display=tab==='disco'?'':'none';
  document.querySelectorAll('#page-quiebre .nav-tab').forEach(function(b){b.style.borderBottomColor='transparent';b.style.color='var(--text2)';});
  btn.style.borderBottomColor='var(--gold)';btn.style.color='var(--gold)';
}

// PLANIFICADOR
let planCatActive='';
function setPlanCat(cat,btn){
  planCatActive=cat;
  document.querySelectorAll('.plan-cat-card').forEach(function(c){c.style.outline='none';});
  document.getElementById('planBtnAll').classList.remove('active');
  if(btn){if(cat==='')btn.classList.add('active');else btn.style.outline='2px solid white';}
  renderPlanificador();
}

function planUrgBadge(u){
  const cfg={urgente:['var(--red)','Urgente'],critico:['var(--orange)','Critico'],planificar:['var(--gold)','Planificar'],analizar:['var(--blue)','Analizar']};
  const cv=cfg[u]||['var(--text2)',u];
  return '<span style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:3px;font-size:10px;font-weight:600;background:'+cv[0]+'18;color:'+cv[0]+'">'+cv[1]+'</span>';
}

function renderPlanificador(){
  const sort=document.getElementById('planSort').value;
  const urgOrder={urgente:0,critico:1,planificar:2,analizar:3};
  const counts={urgente:0,critico:0,planificar:0,analizar:0};
  PURCHASE_PLAN.forEach(function(p){counts[p.urgency]=(counts[p.urgency]||0)+1;});
  document.getElementById('plan-urgente').textContent=counts.urgente||0;
  document.getElementById('plan-critico').textContent=counts.critico||0;
  document.getElementById('plan-planificar').textContent=counts.planificar||0;
  document.getElementById('plan-analizar').textContent=counts.analizar||0;
  const inversion=PURCHASE_PLAN.filter(function(p){return p.inversion;}).reduce(function(s,p){return s+(p.inversion||0);},0);
  document.getElementById('plan-inversion').textContent=fmt(inversion);
  document.getElementById('plan-total').textContent=PURCHASE_PLAN.length+' SKUs';
  let filtered=[...PURCHASE_PLAN];
  if(planCatActive)filtered=filtered.filter(function(p){return p.urgency===planCatActive;});
  filtered.sort(function(a,b){
    if(sort==='urgency')return(urgOrder[a.urgency]||9)-(urgOrder[b.urgency]||9);
    if(sort==='dias_stock')return(a.dias_stock||999)-(b.dias_stock||999);
    if(sort==='vel_mensual')return b.vel_mensual-a.vel_mensual;
    if(sort==='qty_sugerida')return b.qty_sugerida-a.qty_sugerida;
    if(sort==='inversion')return(b.inversion||0)-(a.inversion||0);
    return 0;
  });
  document.getElementById('plan-count').textContent=filtered.length+' productos';
  document.getElementById('plan-tbody').innerHTML=filtered.map(function(p){
    return '<tr data-sku="'+p.sku+'">'
      +'<td><div class="pcell"><div><div class="ptitle">'+p.title+'</div><div class="psku">'+p.sku+'</div></div></div></td>'
      +'<td class="tr" style="color:'+stockValColor(p.stock)+';font-weight:'+(p.stock===0?700:400)+'">'+p.stock+'</td>'
      +'<td class="tr" style="color:'+planDaysColor(p.dias_stock)+';font-weight:600">'+p.dias_stock+'d</td>'
      +'<td class="tr">'+p.vel_mensual.toFixed(1)+'/mes</td>'
      +'<td class="tr" style="color:var(--gold);font-weight:600">'+p.qty_sugerida+'</td>'
      +'<td class="tr">'+(p.inversion?fmt(p.inversion):'--')+'</td>'
      +'<td>'+planUrgBadge(p.urgency)+'</td>'
      +'<td class="tr" style="color:var(--text3)">'+p.last_sale+'</td>'
      +'</tr>';
  }).join('')||'<tr><td colspan="8" style="padding:20px;color:var(--text3)">Sin productos en esta categoria</td></tr>';
}

// BI
function renderBI(){
  const rev=ALL_PRODUCTS.reduce(function(s,p){return s+p.revenue;},0);
  document.getElementById('bi-rev-sku').textContent=fmt(rev/Math.max(ALL_PRODUCTS.length,1));
  document.getElementById('bi-health').textContent=(ALL_PRODUCTS.reduce(function(s,p){return s+p.health;},0)/Math.max(ALL_PRODUCTS.length,1)).toFixed(1);
  document.getElementById('bi-quiebre').textContent=QUIEBRE_DATA.length;
  let cum=0,n80=0;
  for(const p of ALL_PRODUCTS){cum+=p.revenue;n80++;if(cum>=rev*0.8)break;}
  document.getElementById('bi-p80').textContent=n80+' SKUs';
  const cMap={};
  ALL_PRODUCTS.forEach(function(p){Object.entries(p.by_canal||{}).forEach(function(e){const c=e[0],v=e[1];cMap[c]=cMap[c]||{rev:0,units:0};cMap[c].rev+=v.revenue;cMap[c].units+=v.units;});});
  const totalC=Object.values(cMap).reduce(function(s,v){return s+v.rev;},0);
  document.getElementById('canal-comparison').innerHTML=Object.keys(CANAL_LABELS).map(function(k){
    const d=cMap[k]||{rev:0,units:0};
    const pct=totalC>0?d.rev/totalC*100:0;
    return '<div style="margin-bottom:12px">'
      +'<div style="display:flex;justify-content:space-between;margin-bottom:3px"><span style="font-weight:500">'+(CANAL_LABELS[k]||k)+'</span><span style="color:var(--gold)">'+fmt(d.rev)+' ('+pct.toFixed(1)+'%)</span></div>'
      +'<div style="height:5px;background:var(--s3);border-radius:2px"><div style="height:100%;width:'+pct.toFixed(1)+'%;background:var(--gold);border-radius:2px;opacity:.7"></div></div>'
      +'<div style="font-size:10px;color:var(--text3);margin-top:2px">'+fmtN(d.units)+' uni.</div>'
      +'</div>';
  }).join('');
  filteredPareto=[...ALL_PRODUCTS];paretoPage=1;renderParetoTable();
}

function renderParetoTable(){
  const start=(paretoPage-1)*PAGE;
  const page=filteredPareto.slice(start,start+PAGE);
  const totalRev=ALL_PRODUCTS.reduce(function(s,p){return s+p.revenue;},0);
  let cumRev=filteredPareto.slice(0,start).reduce(function(s,p){return s+p.revenue;},0);
  document.getElementById('pareto-tbody').innerHTML=page.map(function(p,i){
    cumRev+=p.revenue;const cumPct=totalRev>0?cumRev/totalRev*100:0;
    return '<tr data-sku="'+p.sku+'">'
      +'<td style="color:var(--text3)">'+(start+i+1)+'</td>'
      +'<td><div class="ptitle">'+p.title+'</div><div class="psku">'+p.sku+'</div></td>'
      +'<td class="tra">'+fmt(p.revenue)+'</td>'
      +'<td class="tr" style="color:'+cumColor(cumPct)+'">'+cumPct.toFixed(1)+'%</td>'
      +'<td class="tr">'+fmtN(p.units)+'</td>'
      +'<td class="tr">'+p.vel_mensual.toFixed(1)+'/mes</td>'
      +'<td><div class="hring '+hClass(p.health)+'" style="width:28px;height:28px;font-size:10px">'+Math.round(p.health)+'</div></td>'
      +'</tr>';
  }).join('');
  renderPag('pareto-pag',filteredPareto.length,paretoPage,function(n){paretoPage=n;renderParetoTable();});
}

// ML PUBLICACIONES
let mlBrandActive='all';
function setMLBrand(brand,btn){
  mlBrandActive=brand;
  document.querySelectorAll('.ml-brand-tab').forEach(function(b){b.style.borderBottomColor='transparent';b.style.color='var(--text2)';});
  btn.style.borderBottomColor='var(--gold)';btn.style.color='var(--gold)';
  filterML();
}
function setMLCat(cat,btn){
  mlCatActive=cat;
  document.querySelectorAll('.prio-btn').forEach(function(b){b.classList.remove('active');});
  if(btn)btn.classList.add('active');
  // Si se hizo clic en el scorecard (btn=null), activar el botón correspondiente
  if(!btn){
    document.querySelectorAll('.prio-btn').forEach(function(b){
      if((cat===''&&b.classList.contains('all'))||(b.classList.contains(cat)))b.classList.add('active');
    });
  }
  filterML();
}
function filterML(){
  const q=document.getElementById('mlSearch').value.toLowerCase();
  const sort=document.getElementById('mlSort').value;
  filteredML=ML_PUBS.filter(function(p){
    if(q&&!p.title.toLowerCase().includes(q)&&!p.item_id.toLowerCase().includes(q))return false;
    if(mlCatActive&&p.categoria!==mlCatActive)return false;
    if(mlBrandActive&&mlBrandActive!=='all'&&p.brand!==mlBrandActive)return false;
    return true;
  });
  filteredML.sort(function(a,b){
    if(sort==='prioridad')return a.prioridad-b.prioridad;
    if(sort==='visitas_desc')return(b.visitas||0)-(a.visitas||0);
    if(sort==='ventas_desc')return b.ventas-a.ventas;
    if(sort==='conversion_desc')return(b.conversion||0)-(a.conversion||0);
    if(sort==='revenue_desc')return b.revenue-a.revenue;
    return 0;
  });
  // Actualizar scorecard
  const counts={muerto:0,invisible:0,oportunidad:0,ganador:0};
  filteredML.forEach(function(p){counts[p.categoria]=(counts[p.categoria]||0)+1;});
  ['muerto','invisible','oportunidad','ganador'].forEach(function(cat){
    const el=document.getElementById('ml-count-'+cat);
    if(el)el.textContent=counts[cat]||0;
  });
  mlPage=1;renderML();
}

let expandedML=null;
function renderML(){
  const start=(mlPage-1)*PAGE;
  const page=filteredML.slice(start,start+PAGE);
  document.getElementById('ml-count').textContent=filteredML.length+' publicaciones';
  const catLabel={muerto:'Muerto',invisible:'Invisible',oportunidad:'Oportunidad',ganador:'Ganador'};
  const catColor={muerto:'#888',invisible:'var(--red)',oportunidad:'var(--gold)',ganador:'var(--green)'};
  const catBg={muerto:'rgba(68,68,68,.3)',invisible:'rgba(248,113,113,.12)',oportunidad:'rgba(240,180,41,.12)',ganador:'rgba(52,211,153,.12)'};
  const brandLabel={pret:'Pret a Home',lavan:'Casa Lavan','':'--'};
  document.getElementById('ml-tbody').innerHTML=page.map(function(p,i){
    const rowId='mlrow-'+(start+i);
    const isExp=expandedML===rowId;
    let html='<tr onclick="toggleMLExpand(\''+rowId+'\',this)" style="cursor:pointer">'
      +'<td><div class="pcell">'+(p.thumbnail?'<img class="pthumb" src="'+p.thumbnail+'" loading="lazy">':'<div class="pthumb-e"></div>')
      +'<div style="min-width:0"><div class="ptitle" title="'+p.title+'">'
      +(p.permalink?'<a href="'+p.permalink+'" target="_blank" onclick="event.stopPropagation()">'+p.title+'</a>':p.title)
      +'</div><div class="psku">'+p.item_id+(p.sku&&p.sku!==p.item_id?' - SKU: '+p.sku:'')+'</div></div></div></td>'
      +'<td class="tr" style="font-weight:500">'+(p.visitas!=null?fmtN(p.visitas):'<span style="color:var(--text3)">-</span>')+'</td>'
      +'<td class="tr">'+fmtN(p.ventas)+'</td>'
      +'<td class="tr">'+(p.conversion!=null?fmtPct(p.conversion):'<span style="color:var(--text3)">-</span>')+'</td>'
      +'<td class="tra">'+(p.revenue>0?fmt(p.revenue):'<span style="color:var(--text3)">-</span>')+'</td>'
      +'<td><span style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;background:'+catBg[p.categoria]+';color:'+catColor[p.categoria]+'">'+(catLabel[p.categoria]||p.categoria)+'</span></td>'
      +'</tr>';
    if(isExp){
      html+='<tr class="ml-row-expand" id="'+rowId+'-expand"><td colspan="6">'
        +'<div class="ml-expand-content">'
        +'<div class="ml-expand-item"><div class="ml-expand-label">Diagnostico</div><div class="ml-expand-value">'+p.diagnostico+'</div></div>'
        +'<div class="ml-expand-item"><div class="ml-expand-label">Accion sugerida</div><div class="ml-expand-value" style="color:var(--gold)">'+p.accion+'</div></div>'
        +'<div class="ml-expand-item"><div class="ml-expand-label">Precio</div><div class="ml-expand-value">'+(p.price>0?fmt(p.price):'--')+'</div></div>'
        +'<div class="ml-expand-item"><div class="ml-expand-label">Marca</div><div class="ml-expand-value">'+(brandLabel[p.brand]||p.brand)+'</div></div>'
        +'</div></td></tr>';
    }
    return html;
  }).join('')||'<tr><td colspan="6" style="padding:20px;color:var(--text3)">Sin publicaciones</td></tr>';
  renderPag('ml-pag',filteredML.length,mlPage,function(n){mlPage=n;renderML();});
}

function toggleMLExpand(rowId,clickedRow){
  expandedML=expandedML===rowId?null:rowId;
  renderML();
}

// PAGINATION
function renderPag(elId,total,cur,goFn){
  const pages=Math.ceil(total/PAGE);
  const el=document.getElementById(elId);
  if(pages<=1){el.innerHTML='';return;}
  let h='<button onclick="('+goFn+')('+(cur-1)+')" '+(cur===1?'disabled':'')+'>&#8592;</button>';
  for(let i=1;i<=pages;i++){
    if(i===1||i===pages||Math.abs(i-cur)<=2)
      h+='<button class="'+(i===cur?'active':'')+'" onclick="('+goFn+')('+i+')">'+i+'</button>';
    else if(Math.abs(i-cur)===3)
      h+='<span style="color:var(--text3);padding:0 2px">...</span>';
  }
  h+='<button onclick="('+goFn+')('+(cur+1)+')" '+(cur===pages?'disabled':'')+'>&#8594;</button>';
  h+='<span class="pi">'+cur+'/'+pages+'</span>';
  el.innerHTML=h;
}

// GLOBAL FILTERS
let G={desde:DATE_FROM,hasta:DATE_TO,canal:'',marca:''};
function getGFilters(){
  return {desde:document.getElementById('fDesde').value||DATE_FROM,hasta:document.getElementById('fHasta').value||DATE_TO,canal:document.getElementById('fCanal').value,marca:document.getElementById('fMarca').value};
}
function calcPeriod(p,desde,hasta,canal){
  let rev=0,units=0,orders=0;
  const fromM=desde.slice(0,7),toM=hasta.slice(0,7);
  Object.entries(p.by_month||{}).forEach(function(e){
    const m=e[0],v=e[1];
    if(m>=fromM&&m<=toM){
      if(canal){const cv=p.by_canal&&p.by_canal[canal];if(!cv)return;const totalRev=Object.values(p.by_month).reduce(function(s,x){return s+x.revenue;},0)||1;const ratio=cv.revenue/totalRev;rev+=v.revenue*ratio;units+=Math.round(v.units*ratio);}
      else{rev+=v.revenue;units+=v.units;}
    }
  });
  orders=units>0?Math.max(1,Math.round(p.orders*(units/Math.max(p.units,1)))):0;
  return {rev:rev,units:units,orders:orders};
}
function matchesCanalMarca(p,canal,marca){
  if(canal&&!(p.by_canal&&p.by_canal[canal]))return false;
  if(marca){const has=Object.keys(p.by_canal||{}).some(function(c){return c.includes(marca);});if(!has)return false;}
  return true;
}
function getActivity(lastSale){
  const d=lastSale&&lastSale!=='--'?Math.floor((new Date()-new Date(lastSale))/864e5):null;
  if(d===null)return 'sin_ventas';if(d<=30)return 'activo';if(d<=90)return 'lento';return 'inactivo';
}
function applyGlobalFilters(){
  G=getGFilters();
  document.getElementById('gf-status').textContent=(G.canal||G.marca||G.desde!==DATE_FROM||G.hasta!==DATE_TO)?G.desde+' -> '+G.hasta+(G.canal?' - '+G.canal:'')+(G.marca?' - '+G.marca:''):'';
  filterProducts();renderOverview();
  if(document.getElementById('page-bi').classList.contains('active'))renderBI();
}
function resetGlobalFilters(){
  document.getElementById('fDesde').value=DATE_FROM;
  document.getElementById('fHasta').value=DATE_TO;
  document.getElementById('fCanal').value='';
  document.getElementById('fMarca').value='';
  G={desde:DATE_FROM,hasta:DATE_TO,canal:'',marca:''};
  document.getElementById('gf-status').textContent='';
  filterProducts();renderOverview();
}

// NAV
const PAGES_WITH_FILTERS=['overview','productos','bi'];
const PAGE_LABELS={overview:'Overview',productos:'Productos','sku-detail':'SKU Detail','sin-ventas':'Sin Ventas',quiebre:'Quiebre Stock',planificador:'Planificador',bi:'BI & Metricas','ml-pubs':'Publicaciones ML'};

function showPage(id,btn){
  document.querySelectorAll('.page').forEach(function(p){p.classList.remove('active');});
  document.querySelectorAll('.drawer-item').forEach(function(b){b.classList.remove('active');});
  document.getElementById('page-'+id).classList.add('active');
  const target=btn||document.querySelector('[data-page="'+id+'"]');
  if(target)target.classList.add('active');
  document.getElementById('global-filters').style.display=PAGES_WITH_FILTERS.includes(id)?'flex':'none';
  document.getElementById('currentPageLabel').textContent=PAGE_LABELS[id]||id;
  if(id==='overview'){renderOverview();initOverviewCharts();}
  if(id==='bi'){renderBI();initBICharts();}
  if(id==='quiebre')renderQuiebre();
  if(id==='planificador')renderPlanificador();
  if(id==='ml-pubs')filterML();
  closeDrawer();
}

// INIT
filterProducts();
filterNoSales();
renderOverview();
initOverviewCharts();
document.getElementById('global-filters').style.display='flex';
document.getElementById('planBtnAll').classList.add('active');
"""
    # Inyectar el JS en el placeholder
    html = html.replace('<script id="main-js">\n</script>', '<script id="main-js">\n' + main_js + '\n</script>')
    return html


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Genera dashboard BI desde cache")
    parser.add_argument("--output", default="dashboard.html")
    parser.add_argument("--desde",  help="YYYY-MM-DD")
    parser.add_argument("--hasta",  help="YYYY-MM-DD")
    args = parser.parse_args()

    config    = load_config()
    weights   = config["settings"]["health_score_weights"]
    date_from = args.desde or config["settings"]["start_date"]
    date_to   = args.hasta or datetime.now().strftime("%Y-%m-%d")

    sync_file  = DATA_DIR / "last_sync.json"
    sync_state = json.loads(sync_file.read_text()) if sync_file.exists() else {}

    print(f"\n  Generando dashboard: {date_from} → {date_to}")

    stock_source  = config["settings"]["stock_source"]
    tn_pret_cache = load_cache(stock_source)
    stock_items   = tn_pret_cache["items"] if tn_pret_cache else {}
    print(f"  Stock/costo: {len(stock_items)} variantes de {stock_source}")

    all_lines      = []
    monthly_totals = defaultdict(lambda: defaultdict(lambda: {"revenue":0.0,"units":0}))
    daily_totals   = defaultdict(lambda: defaultdict(lambda: {"revenue":0.0,"units":0}))
    canal_totals   = {}
    ml_caches      = {}  # Para build_ml_publications

    for key, cfg in config["channels"].items():
        if not cfg.get("enabled"):
            continue
        cache = load_cache(key)
        if not cache:
            print(f"  Sin cache para {key}, saltando")
            continue

        # Guardar brand en el cache para ML publications
        if cache:
            cache["brand"] = cfg["brand"]

        items  = cache.get("items", {})
        orders = cache.get("orders", [])
        brand  = cfg["brand"]

        if cfg["type"] == "mercadolibre":
            lines = normalize_ml_orders(orders, items, key, brand)
            ml_caches[key] = cache  # guardar para publicaciones
        else:
            lines = normalize_tn_orders(orders, items, key, brand)

        lines = [l for l in lines if date_from <= l["date"] <= date_to]
        all_lines.extend(lines)

        canal_rev = sum(l["revenue"] for l in lines)
        canal_totals[key] = {"revenue": round(canal_rev), "units": sum(l["qty"] for l in lines)}
        for l in lines:
            monthly_totals[l["month"]][key]["revenue"] += l["revenue"]
            monthly_totals[l["month"]][key]["units"]   += l["qty"]
            daily_totals[l["date"]][key]["revenue"]    += l["revenue"]
            daily_totals[l["date"]][key]["units"]      += l["qty"]

        print(f"  {cfg['label']}: {len(lines)} líneas de venta")

    print(f"  Procesando {len(all_lines)} líneas totales...")
    products = build_product_map(all_lines, stock_items, date_from, date_to, weights)
    print(f"  {len(products)} SKUs únicos")

    # Sin ventas
    active_no_sales = []
    if tn_pret_cache:
        sold_skus = set(products.keys())
        for vid, item in stock_items.items():
            sku = item.get("sku") or vid
            if sku not in sold_skus and vid not in sold_skus:
                active_no_sales.append({
                    "sku":        sku,
                    "title":      item.get("title", ""),
                    "price":      item.get("price", 0),
                    "stock":      item.get("stock", 0) or 0,
                    "thumbnail":  "",
                    "permalink":  "",
                    "item_id":    vid,
                    "avg_price":  item.get("price", 0),
                    "created_at": item.get("created_at", ""),
                    "last_sale":  "--",
                })

    # Planificador
    purchase_plan = build_purchase_plan(products)
    print(f"  Planificador: {len(purchase_plan)} productos a reponer")

    # ML Publicaciones
    ml_publications = build_ml_publications(ml_caches, all_lines, date_from, date_to)
    print(f"  ML Publicaciones: {len(ml_publications)} publicaciones analizadas")
    cats = {}
    for p in ml_publications:
        cats[p["categoria"]] = cats.get(p["categoria"], 0) + 1
    for cat, n in sorted(cats.items()):
        print(f"    {cat}: {n}")

    # Monthly serializable
    monthly_out = {}
    for month, canals in sorted(monthly_totals.items()):
        monthly_out[month] = {c: {"revenue": round(v["revenue"],2), "units": v["units"]}
                               for c, v in canals.items()}

    # Daily serializable — solo últimos 60 días
    cutoff_daily = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    daily_out = {}
    for day, canals in sorted(daily_totals.items()):
        if day < cutoff_daily:
            continue
        daily_out[day] = {c: {"revenue": round(v["revenue"],2), "units": v["units"]}
                          for c, v in canals.items()}

    print(f"  Generando HTML...")
    html = generate_html(
        products        = products,
        monthly_totals  = monthly_out,
        daily_totals    = daily_out,
        canal_totals    = canal_totals,
        active_no_sales = active_no_sales,
        purchase_plan   = purchase_plan,
        config          = config,
        date_from       = date_from,
        date_to         = date_to,
        sync_state      = sync_state,
        ml_publications = ml_publications,
    )

    output = ROOT / args.output
    output.write_text(html, encoding="utf-8")
    size   = output.stat().st_size / 1024
    print(f"\n  Dashboard: {output} ({size:.0f} KB)")
    print(f"  Abrilo con: open {args.output}\n")

if __name__ == "__main__":
    main()
