"""
Análisis de Pricing ML — Pret a Home
Ejecutar localmente: python3 ml_price_analysis_FINAL.py
Genera: ml_reporte_pricing.html  (dashboard interactivo)
"""

import requests, time, json, sys
from datetime import datetime, timedelta
from collections import defaultdict

# ── Credenciales ─────────────────────────────────────────────────────────────
USER_ID       = "1255615205"
ACCESS_TOKEN  = "APP_USR-1637574709714032-050818-ad208ec2dcc16a2434827bb0d54b524d-1255615205"
CLIENT_ID     = "1637574709714032"
CLIENT_SECRET = "7YEH6ppegi1GbKGu6DhYJptNDeWBjq1s"
REFRESH_TOKEN = "TG-69fe5ce3cbeed400017ab4fa-1255615205"
DAYS_BACK     = 90
OUTPUT_HTML   = "ml_reporte_pricing.html"

HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

# ── Auth ──────────────────────────────────────────────────────────────────────
def refresh_token():
    global ACCESS_TOKEN, HEADERS
    r = requests.post("https://api.mercadolibre.com/oauth/token", data={
        "grant_type": "refresh_token", "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN,
    })
    if r.status_code == 200:
        ACCESS_TOKEN = r.json()["access_token"]
        HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
        print("✓ Token renovado")
    else:
        print(f"⚠ Refresh falló {r.status_code}: {r.text}")
        sys.exit(1)

def ml_get(url, params=None, retry=True):
    r = requests.get(url, headers=HEADERS, params=params)
    if r.status_code == 401 and retry:
        refresh_token()
        return ml_get(url, params, retry=False)
    if r.status_code == 429:
        time.sleep(3)
        return ml_get(url, params, retry=False)
    return r

# ── Fetch data ────────────────────────────────────────────────────────────────
def get_all_item_ids():
    print("📦 Obteniendo IDs de publicaciones...", flush=True)
    ids, offset = [], 0
    while True:
        r = ml_get(f"https://api.mercadolibre.com/users/{USER_ID}/items/search",
                   {"offset": offset, "limit": 100})
        data = r.json()
        batch = data.get("results", [])
        ids.extend(batch)
        total = data.get("paging", {}).get("total", 0)
        print(f"  {len(ids)}/{total}", flush=True)
        if offset + 100 >= total: break
        offset += 100
        time.sleep(0.3)
    return ids

def get_items_details(ids):
    print(f"🔍 Obteniendo detalles de {len(ids)} publicaciones...", flush=True)
    items = {}
    for i in range(0, len(ids), 20):
        batch = ",".join(ids[i:i+20])
        r = ml_get("https://api.mercadolibre.com/items", {"ids": batch})
        for entry in r.json():
            if entry.get("code") == 200:
                body = entry["body"]
                items[body["id"]] = body
        if i % 100 == 0:
            print(f"  {min(i+20, len(ids))}/{len(ids)}", flush=True)
        time.sleep(0.35)
    print(f"  ✓ {len(items)} items", flush=True)
    return items

def get_orders():
    print(f"📊 Órdenes últimos {DAYS_BACK} días...", flush=True)
    date_from = (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT00:00:00-03:00")
    all_orders, offset = [], 0
    while True:
        r = ml_get("https://api.mercadolibre.com/orders/search", {
            "seller": USER_ID, "order.status": "paid", "sort": "date_desc",
            "order.date_created.from": date_from, "offset": offset, "limit": 50
        })
        data = r.json()
        results = data.get("results", [])
        all_orders.extend(results)
        total = data.get("paging", {}).get("total", 0)
        print(f"  {len(all_orders)}/{min(total,1000)}", flush=True)
        if offset + 50 >= min(total, 1000): break
        offset += 50
        time.sleep(0.3)
    return all_orders

# ── Clasificar ────────────────────────────────────────────────────────────────
LISTING_LABELS = {
    "gold_pro":      "Gold Pro",
    "gold_special":  "Gold Special",
    "gold_premium":  "Gold Premium",
    "silver":        "Silver",
    "bronze":        "Bronze",
    "free":          "Gratuita",
}

def classify_item(item):
    lt       = item.get("listing_type_id", "unknown")
    shipping = item.get("shipping", {})
    return {
        "listing_type":     lt,
        "listing_label":    LISTING_LABELS.get(lt, lt),
        "free_shipping":    shipping.get("free_shipping", False),
        "has_installments": lt in ("gold_pro", "gold_special", "gold_premium"),
        "logistic_type":    shipping.get("logistic_type", "unknown"),
        "is_fulfillment":   shipping.get("logistic_type") == "fulfillment",
        "price":            item.get("price", 0),
        "status":           item.get("status", ""),
        "sold_quantity":    item.get("sold_quantity", 0),
        "title":            item.get("title", ""),
    }

def build_sales_map(orders):
    sales = defaultdict(lambda: {"units": 0, "revenue": 0, "orders": 0})
    for order in orders:
        for oi in order.get("order_items", []):
            iid = oi.get("item", {}).get("id")
            if iid:
                qty = oi.get("quantity", 0)
                sales[iid]["units"]   += qty
                sales[iid]["revenue"] += oi.get("unit_price", 0) * qty
                sales[iid]["orders"]  += 1
    return sales

def group_analysis(items_data, sales_map):
    dims = ["by_listing_type", "by_free_shipping", "by_installments", "by_logistic"]
    groups = {d: defaultdict(lambda: {"items": 0, "units": 0, "revenue": 0, "sellers": 0}) for d in dims}
    rows = []
    for iid, item in items_data.items():
        c = classify_item(item)
        s = sales_map.get(iid, {"units": 0, "revenue": 0, "orders": 0})
        rows.append({**c, "item_id": iid, **s})
        sold = s["units"] > 0

        for key, grp in [
            ("by_listing_type",  c["listing_label"]),
            ("by_free_shipping", "Con envío gratis" if c["free_shipping"] else "Sin envío gratis"),
            ("by_installments",  "Con cuotas s/int." if c["has_installments"] else "Sin cuotas s/int."),
            ("by_logistic",      c["logistic_type"]),
        ]:
            groups[key][grp]["items"]   += 1
            groups[key][grp]["units"]   += s["units"]
            groups[key][grp]["revenue"] += s["revenue"]
            if sold: groups[key][grp]["sellers"] += 1
    return groups, rows

# ── Generar HTML ──────────────────────────────────────────────────────────────
def fmt_money(n):
    return f"${n:,.0f}".replace(",", ".")

def pct(a, b):
    return round(a / b * 100, 1) if b else 0

def build_group_rows(group, title):
    rows_html = ""
    total_rev = sum(v["revenue"] for v in group.values()) or 1
    for k, v in sorted(group.items(), key=lambda x: -x[1]["revenue"]):
        conv = pct(v["sellers"], v["items"])
        share = pct(v["revenue"], total_rev)
        bar = f'<div class="bar" style="width:{share}%"></div>'
        rows_html += f"""
        <tr>
          <td>{k}</td>
          <td class="num">{v['items']}</td>
          <td class="num">{v['units']}</td>
          <td class="num">{fmt_money(v['revenue'])}</td>
          <td class="num">{conv}%</td>
          <td class="bar-cell">{bar}<span>{share}%</span></td>
        </tr>"""
    return f"""
    <div class="card">
      <h2>{title}</h2>
      <table>
        <thead><tr>
          <th>Categoría</th><th>Items</th><th>Unidades</th>
          <th>Revenue</th><th>% con ventas</th><th>Share revenue</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>"""

def top_items_table(rows, n=30):
    sorted_rows = sorted(rows, key=lambda x: -x["revenue"])[:n]
    trs = ""
    for r in sorted_rows:
        ship = "✅" if r["free_shipping"] else "❌"
        inst = "✅" if r["has_installments"] else "❌"
        full = "✅" if r["is_fulfillment"] else "❌"
        trs += f"""<tr>
          <td><a href="https://www.mercadolibre.com.ar/p/{r['item_id']}" target="_blank">{r['item_id']}</a></td>
          <td>{r['title'][:55]}</td>
          <td>{r['listing_label']}</td>
          <td class="center">{ship}</td>
          <td class="center">{inst}</td>
          <td class="center">{full}</td>
          <td class="num">{fmt_money(r['price'])}</td>
          <td class="num">{r['units']}</td>
          <td class="num">{fmt_money(r['revenue'])}</td>
        </tr>"""
    return f"""
    <div class="card">
      <h2>Top {n} publicaciones por revenue ({DAYS_BACK} días)</h2>
      <table>
        <thead><tr>
          <th>ID</th><th>Título</th><th>Tipo</th>
          <th>Envío gratis</th><th>Cuotas s/int.</th><th>Full</th>
          <th>Precio</th><th>Unidades</th><th>Revenue</th>
        </tr></thead>
        <tbody>{trs}</tbody>
      </table>
    </div>"""

def generate_html(groups, rows, total_orders, total_items):
    total_rev = sum(r["revenue"] for r in rows)
    total_units = sum(r["units"] for r in rows)
    active = sum(1 for r in rows if r["units"] > 0)
    generated = datetime.now().strftime("%d/%m/%Y %H:%M")

    kpis = f"""
    <div class="kpi-grid">
      <div class="kpi"><span class="kpi-val">{total_items}</span><span class="kpi-lbl">Publicaciones</span></div>
      <div class="kpi"><span class="kpi-val">{active}</span><span class="kpi-lbl">Con ventas ({DAYS_BACK}d)</span></div>
      <div class="kpi"><span class="kpi-val">{total_orders}</span><span class="kpi-lbl">Órdenes</span></div>
      <div class="kpi"><span class="kpi-val">{total_units:,}</span><span class="kpi-lbl">Unidades vendidas</span></div>
      <div class="kpi"><span class="kpi-val">{fmt_money(total_rev)}</span><span class="kpi-lbl">Revenue total</span></div>
    </div>"""

    tables = (
        build_group_rows(groups["by_listing_type"],  "🏷️ Por tipo de publicación") +
        build_group_rows(groups["by_free_shipping"],  "🚚 Por envío gratis") +
        build_group_rows(groups["by_installments"],   "💳 Por cuotas sin interés") +
        build_group_rows(groups["by_logistic"],       "📦 Por tipo de logística") +
        top_items_table(rows)
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Análisis Pricing ML — Pret a Home</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f0f2f5; color: #1a1a2e; }}
  header {{ background: linear-gradient(135deg, #FFE600 0%, #f5c800 100%);
            padding: 24px 32px; border-bottom: 3px solid #d4a900; }}
  header h1 {{ font-size: 1.6rem; color: #1a1a2e; }}
  header p  {{ font-size: 0.85rem; color: #555; margin-top: 4px; }}
  .container {{ max-width: 1300px; margin: 0 auto; padding: 24px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; margin-bottom: 28px; }}
  .kpi {{ background: white; border-radius: 12px; padding: 20px; text-align: center;
          box-shadow: 0 2px 8px rgba(0,0,0,.07); }}
  .kpi-val {{ display: block; font-size: 1.7rem; font-weight: 700; color: #1a1a2e; }}
  .kpi-lbl {{ font-size: 0.75rem; color: #888; margin-top: 4px; display: block; }}
  .card {{ background: white; border-radius: 12px; padding: 24px; margin-bottom: 24px;
           box-shadow: 0 2px 8px rgba(0,0,0,.07); overflow-x: auto; }}
  .card h2 {{ font-size: 1.05rem; margin-bottom: 16px; color: #333; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
  th {{ background: #f8f9fa; padding: 10px 12px; text-align: left; font-weight: 600;
        border-bottom: 2px solid #e9ecef; white-space: nowrap; }}
  td {{ padding: 9px 12px; border-bottom: 1px solid #f1f3f5; }}
  tr:hover td {{ background: #fffdf0; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .center {{ text-align: center; }}
  .bar-cell {{ min-width: 160px; display: flex; align-items: center; gap: 8px; }}
  .bar {{ height: 12px; background: #FFE600; border-radius: 6px; transition: width .3s; min-width: 2px; }}
  .bar-cell span {{ font-size: 0.8rem; color: #666; white-space: nowrap; }}
  a {{ color: #0066cc; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<header>
  <h1>📊 Análisis de Pricing — Pret a Home (ML)</h1>
  <p>Últimos {DAYS_BACK} días · Generado el {generated}</p>
</header>
<div class="container">
  {kpis}
  {tables}
</div>
</body>
</html>"""

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("  Análisis de Pricing ML — Pret a Home")
    print("=" * 55)
    ids        = get_all_item_ids()
    items_data = get_items_details(ids)
    orders     = get_orders()
    sales_map  = build_sales_map(orders)
    groups, rows = group_analysis(items_data, sales_map)

    html = generate_html(groups, rows, len(orders), len(items_data))
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ Dashboard generado: {OUTPUT_HTML}")
    print(f"   Abrilo en tu browser para ver el análisis completo.")

if __name__ == "__main__":
    main()
