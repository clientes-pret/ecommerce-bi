#!/usr/bin/env python3
"""
Catálogo Pret a Home → Google Sheets
- Crea (o actualiza) un Google Sheet con: SKU, Nombre, Link, Medidas, Stock, Precio
- Incrusta un Apps Script que al presionar "🔄 Actualizar" consulta TN
  y actualiza SOLO stock, precio y agrega productos nuevos
- Muestra cuándo fue la última actualización

Requisitos:
    pip3 install requests google-auth google-auth-oauthlib google-api-python-client

Credenciales Google:
    1. Ir a https://console.cloud.google.com
    2. Crear proyecto → Habilitar "Google Sheets API" y "Google Drive API"
    3. Credenciales → OAuth 2.0 → Tipo: App de escritorio → Descargar JSON
    4. Guardar como credentials.json en la misma carpeta que este script
"""

import json, os, sys, time, requests
from datetime import datetime, timezone, timedelta

# ── Credenciales TN ───────────────────────────────────────────────────────────
STORE_ID = "2625285"
TOKEN    = "7bf4cde46764d96772079d8cb1d10cd644aa35a0"
TN_HEADERS = {
    "Authentication": f"bearer {TOKEN}",
    "User-Agent":     "PretAHome Analytics (admin@pretahome.com.ar)",
}
BASE = f"https://api.tiendanube.com/v1/{STORE_ID}"
TZ_AR = timezone(timedelta(hours=-3))

# ── Google Auth ────────────────────────────────────────────────────────────────
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
except ImportError:
    print("❌ Instalá las dependencias primero:")
    print("   pip3 install requests google-auth google-auth-oauthlib google-api-python-client")
    sys.exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/script.projects",
]

def get_google_creds():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                print("❌ No encontré credentials.json")
                print("   Descargalo desde Google Cloud Console → APIs → Credenciales → OAuth 2.0")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return creds

# ── Fetch TN ───────────────────────────────────────────────────────────────────
def get_all_tn(endpoint, params=None):
    params = params or {}
    results = []
    page = 1
    while True:
        params["page"]     = page
        params["per_page"] = 200
        r = requests.get(f"{BASE}/{endpoint}", headers=TN_HEADERS,
                         params=params, timeout=30)
        if r.status_code == 429:
            print("  Rate limit, esperando 10s…"); time.sleep(10); continue
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        results.extend(data)
        if len(data) < 200:
            break
        page += 1
        time.sleep(0.5)
    return results

def extract_text(val):
    if isinstance(val, dict):
        return val.get("es") or val.get("pt") or next(iter(val.values()), "") or ""
    return str(val) if val else ""

def get_medidas(variant):
    """Extrae medidas/opciones de la variante."""
    parts = []
    for attr in ("values",):
        vals = variant.get(attr, [])
        for v in vals:
            if isinstance(v, dict):
                label = v.get("es") or v.get("name") or ""
                if label:
                    parts.append(label)
            elif v:
                parts.append(str(v))
    return " / ".join(parts) if parts else ""

def build_rows():
    print("\n🏷️  Descargando productos de Tienda Nube…")
    products = get_all_tn("products", {
        "fields": "id,name,variants,handle,published",
        "published": "true",
    })
    print(f"  ✓ {len(products)} productos")

    rows = []
    for prod in products:
        name = extract_text(prod.get("name", ""))
        handle = prod.get("handle") or ""
        if isinstance(handle, dict):
            handle = handle.get("es") or next(iter(handle.values()), "")

        link = f"https://pretahome.com/{handle}" if handle else ""

        for v in prod.get("variants", []):
            sku    = v.get("sku") or ""
            stock  = v.get("stock")
            price  = v.get("price") or ""
            medidas = get_medidas(v)

            # Nombre completo
            full_name = name
            if medidas:
                full_name = f"{name} — {medidas}"

            try:
                price_fmt = f"${float(price):,.2f}" if price else ""
            except Exception:
                price_fmt = str(price)

            rows.append([
                sku,
                full_name,
                link,
                medidas,
                int(stock) if stock is not None else 0,
                price_fmt,
            ])

    # Ordenar por nombre
    rows.sort(key=lambda r: r[1].lower())
    return rows

# ── Apps Script (botón de actualización) ──────────────────────────────────────
# Este script corre en Google y llama a TN directamente desde la nube de Google
APPS_SCRIPT_CODE = f"""
const TN_STORE_ID = "{STORE_ID}";
const TN_TOKEN    = "{TOKEN}";
const TN_BASE     = `https://api.tiendanube.com/v1/${{TN_STORE_ID}}`;

function onOpen() {{
  SpreadsheetApp.getUi()
    .createMenu("🛒 Pret a Home")
    .addItem("🔄 Actualizar stock y precios", "actualizarStockYPrecios")
    .addToUi();
}}

function actualizarStockYPrecios() {{
  const ss     = SpreadsheetApp.getActiveSpreadsheet();
  const sheet  = ss.getSheetByName("Catálogo");
  const ui     = SpreadsheetApp.getUi();

  // Indicador visual
  sheet.getRange("H1").setValue("⏳ Actualizando…").setFontColor("#E65100");
  SpreadsheetApp.flush();

  try {{
    // Traer todos los productos de TN
    const products = fetchAllTN("/products?per_page=200&published=true&fields=id,name,variants,handle");

    // Indexar por SKU
    const tnData = {{}};
    products.forEach(prod => {{
      const handle = typeof prod.handle === "object"
        ? (prod.handle.es || Object.values(prod.handle)[0] || "")
        : (prod.handle || "");
      const link = handle ? `https://pretahome.com/${{handle}}` : "";

      const name = typeof prod.name === "object"
        ? (prod.name.es || Object.values(prod.name)[0] || "")
        : (prod.name || "");

      (prod.variants || []).forEach(v => {{
        const sku = v.sku || "";
        if (!sku) return;
        const medidas = (v.values || []).map(x =>
          typeof x === "object" ? (x.es || x.name || "") : String(x)
        ).filter(Boolean).join(" / ");

        tnData[sku] = {{
          name:    medidas ? `${{name}} — ${{medidas}}` : name,
          link:    link,
          medidas: medidas,
          stock:   v.stock !== null && v.stock !== undefined ? Number(v.stock) : 0,
          price:   v.price ? `$${{parseFloat(v.price).toLocaleString("es-AR", {{minimumFractionDigits:2}})}}` : "",
        }};
      }});
    }});

    // Leer filas actuales del sheet (desde fila 3, después del header)
    const lastRow  = sheet.getLastRow();
    const dataRange = sheet.getRange(3, 1, Math.max(lastRow - 2, 1), 6);
    const data     = dataRange.getValues();

    // SKUs ya en el sheet
    const existingSkus = new Set();
    data.forEach((row, i) => {{
      const sku = String(row[0]).trim();
      if (!sku) return;
      existingSkus.add(sku);

      if (tnData[sku]) {{
        // Actualizar stock (col E = índice 4) y precio (col F = índice 5)
        data[i][4] = tnData[sku].stock;
        data[i][5] = tnData[sku].price;
      }}
    }});

    // Escribir cambios de stock/precio
    dataRange.setValues(data);

    // Agregar productos nuevos (SKUs que están en TN pero no en el sheet)
    const nuevos = [];
    Object.entries(tnData).forEach(([sku, d]) => {{
      if (!existingSkus.has(sku)) {{
        nuevos.push([sku, d.name, d.link, d.medidas, d.stock, d.price]);
      }}
    }});

    if (nuevos.length > 0) {{
      const insertRow = sheet.getLastRow() + 1;
      sheet.getRange(insertRow, 1, nuevos.length, 6).setValues(nuevos);
      // Aplicar formato a filas nuevas
      formatDataRows(sheet, insertRow, nuevos.length);
    }}

    // Timestamp
    const now = new Date();
    const ts  = Utilities.formatDate(now, "America/Argentina/Buenos_Aires",
                                     "dd/MM/yyyy HH:mm") + " hs";
    sheet.getRange("H1").setValue(`✅ Última actualización: ${{ts}}`).setFontColor("#1A5C2A");

    const msg = nuevos.length > 0
      ? `✅ Listo. ${{nuevos.length}} productos nuevos agregados.`
      : "✅ Stock y precios actualizados.";
    ui.alert(msg);

  }} catch(e) {{
    sheet.getRange("H1").setValue("❌ Error al actualizar").setFontColor("#C62828");
    ui.alert("Error: " + e.message);
  }}
}}

function fetchAllTN(path) {{
  const headers = {{
    "Authentication": `bearer ${{TN_TOKEN}}`,
    "User-Agent": "PretAHome Analytics (admin@pretahome.com.ar)",
  }};
  let all  = [];
  let page = 1;
  while (true) {{
    const sep = path.includes("?") ? "&" : "?";
    const url = `${{TN_BASE}}${{path}}${{sep}}page=${{page}}&per_page=200`;
    const res = UrlFetchApp.fetch(url, {{ headers, muteHttpExceptions: true }});
    if (res.getResponseCode() === 429) {{
      Utilities.sleep(10000); continue;
    }}
    const data = JSON.parse(res.getContentText());
    if (!data || data.length === 0) break;
    all = all.concat(data);
    if (data.length < 200) break;
    page++;
    Utilities.sleep(500);
  }}
  return all;
}}

function formatDataRows(sheet, startRow, numRows) {{
  const range = sheet.getRange(startRow, 1, numRows, 6);
  range.setFontFamily("Arial").setFontSize(10).setVerticalAlignment("middle");
  range.setBorder(true, true, true, true, true, true, "#BDBDBD",
    SpreadsheetApp.BorderStyle.SOLID);

  // Alternar colores
  for (let i = 0; i < numRows; i++) {{
    const color = (startRow + i) % 2 === 0 ? "#F1F8E9" : "#FFFFFF";
    sheet.getRange(startRow + i, 1, 1, 6).setBackground(color);
  }}
}}
"""

# ── Crear Google Sheet ─────────────────────────────────────────────────────────
def crear_sheet(creds, rows):
    sheets_svc = build("sheets", "v4", credentials=creds)
    drive_svc  = build("drive",  "v3", credentials=creds)

    print("\n📊 Creando Google Sheet…")

    # Crear el spreadsheet
    body = {
        "properties": {"title": "Catálogo Pret a Home"},
        "sheets": [{"properties": {"title": "Catálogo", "gridProperties": {"frozenRowCount": 2}}}]
    }
    ss = sheets_svc.spreadsheets().create(body=body, fields="spreadsheetId,spreadsheetUrl").execute()
    sid = ss["spreadsheetId"]
    url = ss["spreadsheetUrl"]
    print(f"  ✓ Sheet creado: {url}")

    now_str = datetime.now(TZ_AR).strftime("%d/%m/%Y %H:%M") + " hs"

    # ── Datos a escribir ──
    header_row1 = [["Catálogo de Productos — Pret a Home", "", "", "", "", "",
                     "", f"✅ Última actualización: {now_str}"]]
    header_row2 = [["SKU", "Nombre", "Link", "Medidas", "Stock", "Precio", "", ""]]
    all_values  = header_row1 + header_row2 + rows

    sheets_svc.spreadsheets().values().update(
        spreadsheetId=sid,
        range="Catálogo!A1",
        valueInputOption="RAW",
        body={"values": all_values}
    ).execute()

    nrows = len(rows)

    # ── Formato ──
    sheet_id = 0
    requests_fmt = [
        # Fila 1: título
        {"mergeCells": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                                  "startColumnIndex": 0, "endColumnIndex": 7},
                        "mergeType": "MERGE_ALL"}},
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                                  "startColumnIndex": 0, "endColumnIndex": 7},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": {"red": 0.102, "green": 0.361, "blue": 0.165},
                            "textFormat": {"bold": True, "fontSize": 13,
                                           "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                        }},
                        "fields": "userEnteredFormat"}},
        # Col H fila 1: timestamp
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                                  "startColumnIndex": 7, "endColumnIndex": 8},
                        "cell": {"userEnteredFormat": {
                            "textFormat": {"bold": True, "fontSize": 10,
                                           "foregroundColor": {"red": 0.102, "green": 0.361, "blue": 0.165}},
                            "horizontalAlignment": "RIGHT", "verticalAlignment": "MIDDLE",
                        }},
                        "fields": "userEnteredFormat"}},
        # Fila 2: headers
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                                  "startColumnIndex": 0, "endColumnIndex": 6},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": {"red": 0.18, "green": 0.49, "blue": 0.196},
                            "textFormat": {"bold": True, "fontSize": 10,
                                           "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                            "borders": {k: {"style": "SOLID", "color": {"red": 0.74, "green": 0.74, "blue": 0.74}}
                                        for k in ("top","bottom","left","right")},
                        }},
                        "fields": "userEnteredFormat"}},
        # Filas de datos: fuente + bordes + altura
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 2,
                                  "endRowIndex": 2 + nrows, "startColumnIndex": 0, "endColumnIndex": 6},
                        "cell": {"userEnteredFormat": {
                            "textFormat": {"fontFamily": "Arial", "fontSize": 10},
                            "verticalAlignment": "MIDDLE",
                            "borders": {k: {"style": "SOLID", "color": {"red": 0.74, "green": 0.74, "blue": 0.74}}
                                        for k in ("top","bottom","left","right")},
                        }},
                        "fields": "userEnteredFormat"}},
        # Altura filas
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS",
                                                  "startIndex": 0, "endIndex": 1},
                                        "properties": {"pixelSize": 40}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS",
                                                  "startIndex": 1, "endIndex": 2},
                                        "properties": {"pixelSize": 26}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS",
                                                  "startIndex": 2, "endIndex": 2 + nrows},
                                        "properties": {"pixelSize": 22}, "fields": "pixelSize"}},
        # Anchos de columna
        *[{"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": ci, "endIndex": ci + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"
        }} for ci, w in enumerate([120, 340, 280, 140, 70, 100, 20, 280])],
        # Stock: centrado
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 2,
                                  "endRowIndex": 2 + nrows, "startColumnIndex": 4, "endColumnIndex": 5},
                        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                        "fields": "userEnteredFormat"}},
        # Precio: centrado
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 2,
                                  "endRowIndex": 2 + nrows, "startColumnIndex": 5, "endColumnIndex": 6},
                        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                        "fields": "userEnteredFormat"}},
    ]

    # Bandas de color en datos
    for i in range(nrows):
        color = ({"red": 0.945, "green": 0.973, "blue": 0.914}
                 if i % 2 == 0 else {"red": 1, "green": 1, "blue": 1})
        requests_fmt.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 2 + i, "endRowIndex": 3 + i,
                      "startColumnIndex": 0, "endColumnIndex": 6},
            "cell": {"userEnteredFormat": {"backgroundColor": color}},
            "fields": "userEnteredFormat(backgroundColor)"
        }})

    sheets_svc.spreadsheets().batchUpdate(
        spreadsheetId=sid, body={"requests": requests_fmt}
    ).execute()
    print("  ✓ Formato aplicado")

    # ── Incrustar Apps Script ──────────────────────────────────────────────────
    print("  📝 Incrustando Apps Script (botón de actualización)…")
    try:
        script_svc = build("script", "v1", credentials=creds)
        script_body = {
            "title": "Catálogo Pret a Home — Actualizador",
            "parentId": sid,
        }
        proj = script_svc.projects().create(body=script_body).execute()
        script_id = proj["scriptId"]

        script_svc.projects().updateContent(
            scriptId=script_id,
            body={
                "files": [
                    {"name": "Code", "type": "SERVER_JS", "source": APPS_SCRIPT_CODE},
                    {"name": "appsscript", "type": "JSON", "source": json.dumps({
                        "timeZone": "America/Argentina/Buenos_Aires",
                        "dependencies": {},
                        "exceptionLogging": "STACKDRIVER",
                        "runtimeVersion": "V8",
                    })},
                ]
            }
        ).execute()
        print("  ✓ Apps Script incrustado")
        print("\n⚠️  IMPORTANTE — Activar el botón:")
        print("   1. Abrí el Sheet")
        print("   2. Menú superior → Extensiones → Apps Script")
        print("   3. Ejecutá 'onOpen' una vez para autorizar permisos")
        print("   4. Recargá el Sheet → aparece el menú '🛒 Pret a Home'")
        print("   5. Cualquier persona puede usar ese menú para actualizar\n")
    except Exception as e:
        print(f"  ⚠️  No se pudo incrustar el script automáticamente: {e}")
        # Guardar el código para pegar manualmente
        with open("apps_script_actualizador.js", "w") as f:
            f.write(APPS_SCRIPT_CODE)
        print("  → Guardé el código en apps_script_actualizador.js")
        print("  → Abrí el Sheet → Extensiones → Apps Script → pegalo ahí manualmente")

    print(f"\n✅ ¡Listo!")
    print(f"   🔗 {url}")
    return url

# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    creds = get_google_creds()
    rows  = build_rows()
    print(f"  ✓ {len(rows)} variantes a subir")
    url = crear_sheet(creds, rows)
    print(f"\n📋 Abrí tu Sheet acá:\n   {url}\n")
