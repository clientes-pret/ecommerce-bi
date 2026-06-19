#!/usr/bin/env python3
"""
sync_ga4.py — Descarga datos de Google Analytics 4 para Pret a Home y Casa Lavan

Primera vez:
    python3 sync_ga4.py --auth
    (abre el browser, autorizás con tu Google account, queda guardado el token)

Uso normal:
    python3 sync_ga4.py
    python3 sync_ga4.py --days 30
    python3 sync_ga4.py --canal pret
    python3 sync_ga4.py --canal lavan

Genera: data/cache_ga4_pret.json y data/cache_ga4_lavan.json
"""

import json, argparse, os
from datetime import datetime, timedelta
from pathlib import Path

ROOT       = Path(__file__).parent
DATA_DIR   = ROOT / "data"
CREDS_FILE = ROOT / "client_secret_893453167140-d9a9f7g5k4novumh2tghl7p4u384kis9.apps.googleusercontent.com.json"
TOKEN_FILE = DATA_DIR / "ga4_token.json"

# Property IDs de GA4
GA4_PROPERTIES = {
    "pret":  "376963177",
    "lavan": "483582926",
}

# ─── AUTH ─────────────────────────────────────────────────────────────────────

def get_credentials():
    """Obtiene credenciales OAuth. Si no hay token guardado, abre el browser."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("  Instalando dependencias...")
        os.system("pip3 install google-analytics-data google-auth-oauthlib google-auth-httplib2 --break-system-packages -q")
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
    creds = None

    # Intentar cargar token guardado
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE) as f:
            token_data = json.load(f)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    # Si no hay token o está vencido, renovar
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("  Renovando token de GA4...")
            creds.refresh(Request())
        else:
            print("  Abriendo browser para autorizar GA4...")
            if not CREDS_FILE.exists():
                print(f"  ERROR: No se encontró {CREDS_FILE}")
                print(f"  Copiá el archivo JSON de OAuth al directorio del proyecto.")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        # Guardar token
        DATA_DIR.mkdir(exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("  Token guardado.")

    return creds


# ─── FETCH GA4 ────────────────────────────────────────────────────────────────

def fetch_ga4_data(property_id, creds, days=30):
    """
    Descarga de GA4:
    - Sesiones por página de producto (últimos N días)
    - Transacciones y revenue por ítem
    - Conversión por página
    """
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            RunReportRequest, DateRange, Dimension, Metric, FilterExpression,
            Filter, RunReportResponse
        )
    except ImportError:
        os.system("pip3 install google-analytics-data --break-system-packages -q")
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            RunReportRequest, DateRange, Dimension, Metric, FilterExpression, Filter
        )

    client = BetaAnalyticsDataClient(credentials=creds)
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    property_str = f"properties/{property_id}"

    results = {
        "by_page":   {},   # url → {sessions, users}
        "by_item":   {},   # item_id/sku → {views, purchases, revenue}
        "daily":     {},   # fecha → {sessions, revenue}
        "meta": {
            "property_id": property_id,
            "date_from":   date_from,
            "date_to":     date_to,
            "updated":     datetime.now().isoformat(),
        }
    }

    # ── 1. Sesiones por página de producto ────────────────────────────────────
    print(f"    → Sesiones por página de producto...")
    try:
        req = RunReportRequest(
            property=property_str,
            date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
            dimensions=[
                Dimension(name="pagePath"),
                Dimension(name="pageTitle"),
            ],
            metrics=[
                Metric(name="sessions"),
                Metric(name="totalUsers"),
                Metric(name="screenPageViews"),
            ],
            # Filtrar solo páginas de producto (ajustá el path si cambia)
            dimension_filter=FilterExpression(
                filter=Filter(
                    field_name="pagePath",
                    string_filter=Filter.StringFilter(
                        match_type=Filter.StringFilter.MatchType.CONTAINS,
                        value="/productos/",
                        case_sensitive=False,
                    )
                )
            ),
            limit=10000,
        )
        response = client.run_report(req)
        for row in response.rows:
            path    = row.dimension_values[0].value
            title   = row.dimension_values[1].value
            sessions = int(row.metric_values[0].value)
            users    = int(row.metric_values[1].value)
            views    = int(row.metric_values[2].value)
            results["by_page"][path] = {
                "title":    title,
                "sessions": sessions,
                "users":    users,
                "views":    views,
            }
        print(f"      {len(results['by_page'])} páginas de producto")
    except Exception as e:
        print(f"      Warning: {e}")

    # ── 2. Revenue y compras por ítem (ecommerce) ─────────────────────────────
    print(f"    → Items de ecommerce...")
    try:
        req2 = RunReportRequest(
            property=property_str,
            date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
            dimensions=[
                Dimension(name="itemId"),
                Dimension(name="itemName"),
            ],
            metrics=[
                Metric(name="itemsViewed"),
                Metric(name="itemsPurchased"),
                Metric(name="itemRevenue"),
            ],
            limit=10000,
        )
        response2 = client.run_report(req2)
        for row in response2.rows:
            item_id   = row.dimension_values[0].value
            item_name = row.dimension_values[1].value
            viewed    = int(row.metric_values[0].value)
            purchased = int(row.metric_values[1].value)
            revenue   = float(row.metric_values[2].value)
            if item_id:
                results["by_item"][item_id] = {
                    "name":      item_name,
                    "viewed":    viewed,
                    "purchased": purchased,
                    "revenue":   round(revenue, 2),
                    "conversion": round(purchased / viewed * 100, 2) if viewed > 0 else 0,
                }
        print(f"      {len(results['by_item'])} items con datos de ecommerce")
    except Exception as e:
        print(f"      Warning: {e}")

    # ── 3. Datos diarios (para el chart de 10 días) ───────────────────────────
    print(f"    → Totales diarios...")
    try:
        req3 = RunReportRequest(
            property=property_str,
            date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
            dimensions=[Dimension(name="date")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="totalUsers"),
                Metric(name="transactions"),
                Metric(name="purchaseRevenue"),
            ],
            limit=10000,
        )
        response3 = client.run_report(req3)
        for row in response3.rows:
            raw_date = row.dimension_values[0].value  # formato YYYYMMDD
            date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            results["daily"][date_str] = {
                "sessions":     int(row.metric_values[0].value),
                "users":        int(row.metric_values[1].value),
                "transactions": int(row.metric_values[2].value),
                "revenue":      round(float(row.metric_values[3].value), 2),
            }
        print(f"      {len(results['daily'])} días de datos")
    except Exception as e:
        print(f"      Warning: {e}")

    return results


# ─── GUARDAR CACHE ────────────────────────────────────────────────────────────

def save_cache(brand, data):
    path = DATA_DIR / f"cache_ga4_{brand}.json"
    DATA_DIR.mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = path.stat().st_size / 1024
    print(f"    Guardado: cache_ga4_{brand}.json ({size_kb:.1f} KB)")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync Google Analytics 4")
    parser.add_argument("--auth",  action="store_true", help="Forzar re-autorización")
    parser.add_argument("--canal", choices=["pret", "lavan"], help="Solo un canal")
    parser.add_argument("--days",  type=int, default=60, help="Días a descargar (default: 60)")
    args = parser.parse_args()

    print("\n" + "─"*52)
    print(f"  SYNC GA4 — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("─"*52 + "\n")

    # Limpiar token si se pide re-auth
    if args.auth and TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        print("  Token eliminado, re-autorizando...\n")

    # Obtener credenciales
    creds = get_credentials()
    if not creds:
        return

    print(f"  Credenciales OK\n")

    # Determinar canales a sincronizar
    canales = ["pret", "lavan"] if not args.canal else [args.canal]

    for brand in canales:
        property_id = GA4_PROPERTIES[brand]
        label = "Pret a Home" if brand == "pret" else "Casa Lavan"
        print(f"  [{label}] — property {property_id} — últimos {args.days} días")

        data = fetch_ga4_data(property_id, creds, days=args.days)
        save_cache(brand, data)

        # Resumen
        n_pages = len(data["by_page"])
        n_items = len(data["by_item"])
        n_days  = len(data["daily"])
        total_sessions = sum(v["sessions"] for v in data["daily"].values())
        print(f"    Resumen: {n_pages} páginas, {n_items} items ecommerce, {n_days} días, {total_sessions:,} sesiones\n")

    print("─"*52)
    print("  Sync GA4 completo. Corré: python3 report.py")
    print("─"*52 + "\n")


if __name__ == "__main__":
    main()
