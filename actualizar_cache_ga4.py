"""
actualizar_cache_ga4.py
Regenera cache_ga4_pret.json con los últimos 60 días.
Incluye: by_page, by_item, daily, by_channel (nuevo)

Uso:
    python3 actualizar_cache_ga4.py

Requiere:
    pip install google-analytics-data google-auth
"""

import json
import os
import requests
from datetime import datetime, timedelta
from pathlib import Path

# ── Configuración ────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent
TOKEN_FILE   = SCRIPT_DIR / "data" / "ga4_token.json"
OUTPUT_FILE  = SCRIPT_DIR / "data" / "cache_ga4_pret.json"
PROPERTY_ID  = "376963177"
DAYS         = 60

# ── Helpers ──────────────────────────────────────────────────────────────────

def refresh_token(tok: dict) -> str:
    """Obtiene un access token fresco usando el refresh token."""
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id":     tok["client_id"],
        "client_secret": tok["client_secret"],
        "refresh_token": tok["refresh_token"],
        "grant_type":    "refresh_token",
    })
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Error al refrescar token: {data}")
    print(f"✓ Token refrescado (expira en {data.get('expires_in', '?')}s)")
    return data["access_token"]


def run_report(access_token: str, property_id: str, date_from: str, date_to: str,
               dimensions: list, metrics: list,
               limit: int = 100_000) -> list[dict]:
    """Corre un reporte de GA4 Data API y devuelve lista de filas como dicts."""
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    body = {
        "dateRanges": [{"startDate": date_from, "endDate": date_to}],
        "dimensions": [{"name": d} for d in dimensions],
        "metrics":    [{"name": m} for m in metrics],
        "limit": limit,
    }
    resp = requests.post(url, headers=headers, json=body)
    resp.raise_for_status()
    data = resp.json()

    dim_headers = [h["name"] for h in data.get("dimensionHeaders", [])]
    met_headers = [h["name"] for h in data.get("metricHeaders", [])]
    rows = []
    for row in data.get("rows", []):
        r = {}
        for i, dv in enumerate(row.get("dimensionValues", [])):
            r[dim_headers[i]] = dv["value"]
        for i, mv in enumerate(row.get("metricValues", [])):
            r[met_headers[i]] = mv["value"]
        rows.append(r)
    print(f"  → {len(rows)} filas ({', '.join(dim_headers)})")
    return rows


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Fechas
    date_to   = datetime.today().strftime("%Y-%m-%d")
    date_from = (datetime.today() - timedelta(days=DAYS)).strftime("%Y-%m-%d")
    print(f"\n📅 Período: {date_from} → {date_to} ({DAYS} días)\n")

    # Token
    with open(TOKEN_FILE) as f:
        tok = json.load(f)
    access_token = refresh_token(tok)

    cache = {"meta": {
        "property_id": PROPERTY_ID,
        "date_from":   date_from,
        "date_to":     date_to,
        "updated":     datetime.now().isoformat(),
    }}

    # ── 1. by_page ────────────────────────────────────────────────────────────
    print("\n[1/4] by_page — tráfico por URL de producto...")
    rows = run_report(access_token, PROPERTY_ID, date_from, date_to,
        dimensions=["pagePath", "pageTitle"],
        metrics=["sessions", "totalUsers", "screenPageViews"]
    )
    by_page = {}
    for r in rows:
        path = r["pagePath"]
        if not path.startswith("/productos/"):
            continue
        by_page[path] = {
            "title":    r["pageTitle"],
            "sessions": int(r["sessions"]),
            "users":    int(r["totalUsers"]),
            "views":    int(r["screenPageViews"]),
        }
    cache["by_page"] = by_page
    print(f"  ✓ {len(by_page)} páginas de producto")

    # ── 2. by_item ────────────────────────────────────────────────────────────
    print("\n[2/4] by_item — vistas y compras por variante...")
    rows = run_report(access_token, PROPERTY_ID, date_from, date_to,
        dimensions=["itemId", "itemName"],
        metrics=["itemsViewed", "itemsPurchased", "itemRevenue"]
    )
    by_item = {}
    for r in rows:
        item_id  = r["itemId"]
        viewed   = int(r["itemsViewed"])
        purchased = int(r["itemsPurchased"])
        revenue  = float(r["itemRevenue"])
        conv     = round(purchased / viewed * 100, 2) if viewed > 0 else 0.0
        by_item[item_id] = {
            "name":       r["itemName"],
            "viewed":     viewed,
            "purchased":  purchased,
            "revenue":    revenue,
            "conversion": conv,
        }
    cache["by_item"] = by_item
    print(f"  ✓ {len(by_item)} items")

    # ── 3. daily ──────────────────────────────────────────────────────────────
    print("\n[3/4] daily — métricas diarias...")
    rows = run_report(access_token, PROPERTY_ID, date_from, date_to,
        dimensions=["date"],
        metrics=["sessions", "totalUsers", "transactions", "purchaseRevenue"]
    )
    daily = {}
    for r in rows:
        d = r["date"]
        date_fmt = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        daily[date_fmt] = {
            "sessions":     int(r["sessions"]),
            "users":        int(r["totalUsers"]),
            "transactions": int(r["transactions"]),
            "revenue":      float(r["purchaseRevenue"]),
        }
    cache["daily"] = daily
    print(f"  ✓ {len(daily)} días")

    # ── 4. by_channel (NUEVO) ─────────────────────────────────────────────────
    print("\n[4/4] by_channel — sesiones y revenue por canal...")
    rows = run_report(access_token, PROPERTY_ID, date_from, date_to,
        dimensions=["sessionDefaultChannelGroup"],
        metrics=["sessions", "totalUsers", "transactions", "purchaseRevenue"]
    )
    by_channel = {}
    for r in rows:
        channel = r["sessionDefaultChannelGroup"]
        by_channel[channel] = {
            "sessions":     int(r["sessions"]),
            "users":        int(r["totalUsers"]),
            "transactions": int(r["transactions"]),
            "revenue":      float(r["purchaseRevenue"]),
        }
    cache["by_channel"] = by_channel
    print(f"  ✓ {len(by_channel)} canales: {list(by_channel.keys())}")

    # ── Guardar ───────────────────────────────────────────────────────────────
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Cache guardado en {OUTPUT_FILE}")
    print(f"   by_page:    {len(by_page):>5} URLs")
    print(f"   by_item:    {len(by_item):>5} items")
    print(f"   daily:      {len(daily):>5} días")
    print(f"   by_channel: {len(by_channel):>5} canales")


if __name__ == "__main__":
    main()
