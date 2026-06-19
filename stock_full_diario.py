#!/usr/bin/env python3
"""
stock_full_diario.py — Stock Full ML Pret a Home
Filas = publicaciones | Columnas = fechas (04/06, 05/06...)
Cada vez que corre agrega la columna del día. Nunca pisa datos anteriores.

Uso:
    source venv/bin/activate
    pip install gspread google-auth google-auth-oauthlib google-api-python-client
    python3 stock_full_diario.py

Cron (medianoche):
    0 0 * * * cd ~/Desktop/ecommerce-bi && source venv/bin/activate && python3 stock_full_diario.py >> logs/stock_full.log 2>&1
"""

import json, requests, time, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

ROOT        = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
TOKEN_PATH  = ROOT / "token.json"
CREDS_PATH  = ROOT / "credentials.json"

SHEET_NAME  = "Stock Full ML — Pret a Home"
TAB_NAME    = "Stock Full"

TZ_AR = timezone(timedelta(hours=-3))
HOY   = datetime.now(TZ_AR).strftime("%d/%m/%Y")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ─── GOOGLE AUTH (mismo patrón que catalogo_google_sheets.py) ─────────────────

def get_google_creds():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow  = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return creds

def get_sheets_service():
    from googleapiclient.discovery import build
    creds = get_google_creds()
    return build("sheets", "v4", credentials=creds)

def get_drive_service():
    from googleapiclient.discovery import build
    creds = get_google_creds()
    return build("drive", "v3", credentials=creds)

# ─── HELPERS ML ───────────────────────────────────────────────────────────────

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def ml_refresh(cfg):
    r = requests.post("https://api.mercadolibre.com/oauth/token", data={
        "grant_type":    "refresh_token",
        "client_id":     cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "refresh_token": cfg["refresh_token"],
    }, timeout=15)
    if r.status_code == 200:
        return r.json()["access_token"]
    print(f"  ⚠️  Refresh falló ({r.status_code}), usando token existente")
    return cfg["access_token"]

def ml_get(url, token, params=None, retries=4):
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500):
                time.sleep(3 * (attempt + 1))
                continue
            return None
        except Exception:
            time.sleep(2)
    return None

# ─── TRAER IDs (scroll sin límite) ───────────────────────────────────────────

def get_all_item_ids(token, user_id):
    item_ids  = []
    scroll_id = None
    total     = None

    while True:
        params = {"limit": 100, "status": "active", "search_type": "scan"}
        if scroll_id:
            params["scroll_id"] = scroll_id

        data = ml_get(
            f"https://api.mercadolibre.com/users/{user_id}/items/search",
            token, params=params
        )
        if not data:
            break

        if total is None:
            total = data.get("paging", {}).get("total", 0)
            print(f"  Total publicaciones activas: {total}")

        batch = data.get("results", [])
        if not batch:
            break

        item_ids.extend(batch)
        scroll_id = data.get("scroll_id")

        if len(item_ids) >= total or not scroll_id:
            break
        time.sleep(0.3)

    print(f"  ✓ {len(item_ids)} IDs recuperados")
    return list(dict.fromkeys(item_ids))

# ─── DETALLE DE ITEMS ─────────────────────────────────────────────────────────
# Trae título, SKU, logistic_type y available_quantity en un solo paso.
# stock Full = available_quantity cuando logistic_type == "fulfillment"

def get_items_detail(token, item_ids):
    details = {}
    total   = len(item_ids)
    for i in range(0, total, 20):
        batch = item_ids[i:i+20]
        data  = ml_get("https://api.mercadolibre.com/items", token,
                        params={"ids": ",".join(batch)})
        if data:
            for entry in data:
                if entry.get("code") == 200:
                    body = entry["body"]
                    sku  = body.get("seller_custom_field") or ""
                    if not sku:
                        for attr in body.get("attributes", []):
                            if attr.get("id") in ("SELLER_SKU", "SKU"):
                                sku = attr.get("value_name") or ""
                                break
                    logistic = body.get("shipping", {}).get("logistic_type", "")
                    qty      = body.get("available_quantity", 0)
                    details[body["id"]] = {
                        "title":        body.get("title", ""),
                        "sku":          sku,
                        "logistic_type": logistic,
                        # Si es Full, el stock disponible ES el stock Full
                        "stock_full":   qty if logistic == "fulfillment" else 0,
                    }
        if (i // 20 + 1) % 50 == 0:
            print(f"  Detalle: {min(i+20, total)}/{total}")
        time.sleep(0.25)
    print(f"  ✓ Detalle de {len(details)} items")
    return details

# ─── GOOGLE SHEETS — crear o abrir ───────────────────────────────────────────

def get_or_create_spreadsheet(svc_sheets, svc_drive):
    # Buscar si ya existe
    res = svc_drive.files().list(
        q=f"name='{SHEET_NAME}' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
        fields="files(id,name)"
    ).execute()

    files = res.get("files", [])
    if files:
        sid = files[0]["id"]
        print(f"  ✓ Spreadsheet encontrado: {sid}")
    else:
        body = {"properties": {"title": SHEET_NAME}}
        sh   = svc_sheets.spreadsheets().create(body=body, fields="spreadsheetId").execute()
        sid  = sh["spreadsheetId"]
        print(f"  ✓ Spreadsheet creado: {sid}")

    return sid

def get_or_create_tab(svc_sheets, spreadsheet_id):
    meta = svc_sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == TAB_NAME:
            return s["properties"]["sheetId"]

    # Crear pestaña
    req = {"addSheet": {"properties": {"title": TAB_NAME, "gridProperties": {"rowCount": 5000, "columnCount": 500}}}}
    res = svc_sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [req]}
    ).execute()
    sid = res["replies"][0]["addSheet"]["properties"]["sheetId"]
    print(f"  ✓ Pestaña '{TAB_NAME}' creada")
    return sid

# ─── ESCRIBIR DATOS ───────────────────────────────────────────────────────────

def col_letter(n):
    """1→A, 26→Z, 27→AA"""
    result = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result

def escribir(svc, spreadsheet_id, rows_data, hoy):
    # Leer estado actual
    result = svc.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{TAB_NAME}'"
    ).execute()
    all_vals = result.get("values", [])

    COLS_FIJAS = 4  # A=ID, B=ML#, C=Título, D=SKU

    # Inicializar header si está vacío
    if not all_vals:
        svc.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{TAB_NAME}'!A1:D1",
            valueInputOption="RAW",
            body={"values": [["ID Publicación", "ML #", "Título", "SKU"]]}
        ).execute()
        all_vals = [["ID Publicación", "ML #", "Título", "SKU"]]
        print("  ✓ Header creado")

    headers = all_vals[0]

    # Buscar o crear columna de hoy
    if hoy in headers:
        col_hoy = headers.index(hoy)  # 0-based
    else:
        col_hoy = len(headers)
        headers.append(hoy)
        # Escribir solo el header de la fecha nueva
        cell = f"'{TAB_NAME}'!{col_letter(col_hoy + 1)}1"
        svc.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=cell,
            valueInputOption="RAW",
            body={"values": [[hoy]]}
        ).execute()

    # Mapa ID → índice fila (0-based en all_vals, 0 = header)
    id_a_fila = {}
    for i, row in enumerate(all_vals[1:], 1):
        if row and row[0]:
            id_a_fila[row[0]] = i

    # Preparar batch update
    value_updates = []
    filas_nuevas  = []

    for item in rows_data:
        item_id = item["item_id"]
        ml_num  = item_id.replace("MLA", "")

        if item_id in id_a_fila:
            fila = id_a_fila[item_id] + 1  # 1-based para Sheets
            # Actualizar título y SKU
            value_updates.append({
                "range": f"'{TAB_NAME}'!C{fila}:D{fila}",
                "values": [[item["titulo"], item["sku"]]]
            })
            # Escribir stock del día
            value_updates.append({
                "range": f"'{TAB_NAME}'!{col_letter(col_hoy + 1)}{fila}",
                "values": [[item["stock_full"]]]
            })
        else:
            nueva = [""] * max(COLS_FIJAS, col_hoy + 1)
            nueva[0] = item_id
            nueva[1] = ml_num
            nueva[2] = item["titulo"]
            nueva[3] = item["sku"]
            nueva[col_hoy] = item["stock_full"]
            filas_nuevas.append(nueva)

    # Escribir filas nuevas
    if filas_nuevas:
        next_row  = len(all_vals) + 1
        max_cols  = max(len(r) for r in filas_nuevas)
        filas_norm = [r + [""] * (max_cols - len(r)) for r in filas_nuevas]
        end_col   = col_letter(max_cols)
        svc.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{TAB_NAME}'!A{next_row}:{end_col}{next_row + len(filas_nuevas) - 1}",
            valueInputOption="RAW",
            body={"values": filas_norm}
        ).execute()
        print(f"  ✓ {len(filas_nuevas)} publicaciones nuevas")

    # Batch update de filas existentes (máx 1000 por llamada)
    if value_updates:
        for i in range(0, len(value_updates), 500):
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "RAW", "data": value_updates[i:i+500]}
            ).execute()
        print(f"  ✓ {len(id_a_fila)} filas existentes actualizadas")

    # Formato: congelar filas/columnas y colorear header
    aplicar_formato(svc, spreadsheet_id, col_hoy)

    print(f"  🔗 https://docs.google.com/spreadsheets/d/{spreadsheet_id}")

def aplicar_formato(svc, spreadsheet_id, col_hoy):
    """Congela primera fila y primeras 4 columnas. Colorea header de hoy."""
    meta   = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    tab_id = next(s["properties"]["sheetId"] for s in meta["sheets"]
                  if s["properties"]["title"] == TAB_NAME)

    requests_fmt = [
        # Congelar fila 1
        {"updateSheetProperties": {
            "properties": {"sheetId": tab_id,
                           "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 4}},
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
        }},
        # Header fijo (cols A-D) azul oscuro
        {"repeatCell": {
            "range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": 4},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.102, "green": 0.137, "blue": 0.494},
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1},
                               "bold": True},
                "horizontalAlignment": "CENTER"
            }},
            "fields": "userEnteredFormat"
        }},
        # Header columna de hoy — azul más claro
        {"repeatCell": {
            "range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": col_hoy, "endColumnIndex": col_hoy + 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.255, "green": 0.565, "blue": 0.933},
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1},
                               "bold": True},
                "horizontalAlignment": "CENTER"
            }},
            "fields": "userEnteredFormat"
        }},
    ]

    svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests_fmt}
    ).execute()

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'═'*52}")
    print(f"  STOCK FULL ML — PRET A HOME")
    print(f"  Fecha: {HOY}")
    print(f"{'═'*52}\n")

    # 1. Config y token ML
    config   = load_config()
    cfg_pret = config["channels"]["ml_pret"]
    user_id  = cfg_pret["user_id"]

    print("1. Renovando token ML...")
    token = ml_refresh(cfg_pret)
    print("   ✓ Token listo\n")

    # 2. IDs
    print("2. Trayendo publicaciones activas...")
    item_ids = get_all_item_ids(token, user_id)
    print()

    # 3. Detalle + stock Full (logistic_type == fulfillment → available_quantity es stock Full)
    print("3. Trayendo detalle y stock Full...")
    details = get_items_detail(token, item_ids)
    print()

    # 4. Armar rows
    rows_data = []
    for item_id in item_ids:
        det = details.get(item_id, {})
        rows_data.append({
            "item_id":    item_id,
            "titulo":     det.get("title", ""),
            "sku":        det.get("sku",   ""),
            "stock_full": det.get("stock_full", 0),
        })
    rows_data.sort(key=lambda r: -r["stock_full"])

    con_full   = sum(1 for r in rows_data if r["stock_full"] > 0)
    total_full = sum(r["stock_full"] for r in rows_data)
    print(f"   Con stock Full: {con_full}/{len(rows_data)} | Unidades: {total_full:,}\n")

    # 6. Google Sheets
    print("5. Conectando a Google Sheets...")
    svc_sheets = get_sheets_service()
    svc_drive  = get_drive_service()
    sid        = get_or_create_spreadsheet(svc_sheets, svc_drive)
    get_or_create_tab(svc_sheets, sid)
    print("   Escribiendo datos...")
    escribir(svc_sheets, sid, rows_data, HOY)

    print(f"\n  ✅ Listo — '{SHEET_NAME}' → '{TAB_NAME}'\n")
    print(f"{'═'*52}\n")

if __name__ == "__main__":
    main()
