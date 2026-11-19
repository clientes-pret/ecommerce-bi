"""Llena la planilla oficial de ML (cortinas manuales) a partir de la PUBLICACIÓN DE PRET A HOME en Mercado Libre.
Prototipo v3 (18/11/2026). Reemplaza a v1 (basada en Tiendanube): una publicación de Pret = un bloque de filas de la planilla, con
el MISMO título y la misma ficha técnica en todas las filas, y una fila por color con su SKU, su código de barras (GTIN) y SUS fotos.
Los datos salen de `GET /items/{id}?include_attributes=all` y `/items/{id}/description` con el acceso de Pret (cuenta propia, conectada
en modo solo lectura: fila 'pausado' de rev_revendedores; no es el acceso de producción).
Edita solo celdas de datos de la hoja 'Manuales' del .xlsx (se conserva hoja oculta, listas, fórmulas e imágenes).
Uso: python3 llenar_planilla_ml_v3.py PLANTILLA.xlsx SALIDA.xlsx MAX_COLORES MLA_DE_PRET [MLA_DE_PRET ...]"""
import json, re, sys, zipfile, html, unicodedata, urllib.request, urllib.parse, urllib.error

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
cfg = json.load(open(f"{ROOT}/config.json")); sb = cfg["supabase"]
H = {"apikey": sb["service_role_key"], "Authorization": "Bearer " + sb["service_role_key"]}
PLANTILLA, SALIDA, MAX_COLORES = sys.argv[1], sys.argv[2], int(sys.argv[3])
ITEMS = sys.argv[4:]
PRET_USER_ID = 1255615205
FILA_INICIAL = 8
HOJA = "xl/worksheets/sheet3.xml"  # 'Manuales' (categoría MLA4771)


def db(p):
    return json.load(urllib.request.urlopen(urllib.request.Request(sb["url"] + "/rest/v1/" + p, headers=H), timeout=60))


TOKEN = db(f"rev_revendedores?ml_user_id=eq.{PRET_USER_ID}&select=access_token")[0]["access_token"]


def ml(path):
    try:
        r = urllib.request.urlopen(urllib.request.Request("https://api.mercadolibre.com" + path, headers={"Authorization": "Bearer " + TOKEN}), timeout=40)
        return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200].decode("utf-8", "replace")


sin_acentos = lambda s: "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower().strip()


def medida(valor):
    """'2.1 m' -> (2.1, 'm'); '140 cm' -> (140.0, 'cm')"""
    m = re.match(r"\s*([\d.,]+)\s*(cm|m)\b", str(valor or ""))
    return (float(m.group(1).replace(",", ".")), m.group(2)) if m else (None, None)


# ---------- 1. Datos de las publicaciones de Pret
grupos = []
for mla in ITEMS:
    s, it = ml(f"/items/{mla}?include_attributes=all")
    if s != 200:
        print(f"[!] {mla}: HTTP {s}; se omite"); continue
    if it["category_id"] != "MLA4771":
        print(f"[!] {mla}: categoría {it['category_id']} (esta planilla es de MLA4771); se omite"); continue
    s, d = ml(f"/items/{mla}/description")
    desc = (d.get("plain_text") or "").strip()[:4000] if isinstance(d, dict) else ""
    at = {a["id"]: a.get("value_name") for a in it.get("attributes", []) if a.get("value_name")}
    pics = {p["id"]: (p.get("secure_url") or p.get("url")) for p in it.get("pictures", [])}
    filas = []
    for v in it.get("variations", []):
        va = {a["id"]: a.get("value_name") for a in v.get("attributes", []) if a.get("value_name")}
        sku = va.get("SELLER_SKU")
        if not sku: continue
        en_cat = db(f"rev_catalogo?sku=eq.{urllib.parse.quote(sku)}&habilitado=eq.true&select=sku")
        of = db(f"rev_stock_ofrecido?sku=eq.{urllib.parse.quote(sku)}&select=ofrecido")
        if not en_cat or not of or (of[0]["ofrecido"] or 0) < 1: continue
        color = next((a.get("value_name") for a in v.get("attribute_combinations", []) if a.get("id") == "COLOR"), None) or va.get("MAIN_COLOR")
        fotos = [pics[i] for i in v.get("picture_ids", []) if i in pics][:10]
        if not color or not fotos: continue
        filas.append(dict(sku=sku, color=color, fotos=fotos, gtin=va.get("GTIN"), precio=int(round(float(v.get("price") or it["price"]))), ofrecido=of[0]["ofrecido"]))
    filas = filas[:MAX_COLORES]
    if not filas:
        print(f"[!] {mla}: ninguna variante con SKU en el catálogo y stock ofrecido; se omite"); continue
    alto, u_alto = medida(at.get("HEIGHT")); ancho, u_ancho = medida(at.get("WIDTH"))
    grupos.append(dict(mla=mla, titulo=it["title"][:60], at=at, alto=alto, u_alto=u_alto, ancho=ancho, u_ancho=u_ancho, desc=desc, variantes=filas))

# ---------- 2. Edición del xlsx
z = zipfile.ZipFile(PLANTILLA)
partes = {n: z.read(n) for n in z.namelist()}
orden = [i.filename for i in z.infolist()]
hoja = partes[HOJA].decode("utf-8")
sst = partes["xl/sharedStrings.xml"].decode("utf-8")
n_unico = int(re.search(r'uniqueCount="(\d+)"', sst).group(1)); n_cuenta = int(re.search(r'<sst[^>]*\scount="(\d+)"', sst).group(1))
nuevas_si = []


def ss(texto):
    nuevas_si.append(f'<si><t xml:space="preserve">{html.escape(texto, quote=False)}</t></si>')
    return n_unico + len(nuevas_si) - 1


todas_si = re.findall(r"<si>(.*?)</si>", sst, re.S)
texto_si = lambda x: html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", x, re.S)))
textos = [texto_si(x) for x in todas_si]
lista_colores = {}
if "Coral claro" in textos:
    k = textos.index("Coral claro")
    for t in textos[k:k + 51]: lista_colores[sin_acentos(t)] = t
color_ml = lambda c: lista_colores.get(sin_acentos(c), c)

col_style = {}
for m in re.finditer(r'<col min="(\d+)" max="(\d+)" style="(\d+)"', hoja[:6000]):
    for k in range(int(m.group(1)), int(m.group(2)) + 1): col_style[k] = m.group(3)


def num_col(letras):
    n = 0
    for ch in letras: n = n * 26 + ord(ch) - 64
    return n


def letras_col(n):
    s = ""
    while n: n, r = divmod(n - 1, 26); s = chr(65 + r) + s
    return s


CELDA = re.compile(r'<c r="([A-Z]+)(\d+)"((?:[^>/]|/(?!>))*)(?:/>|>(.*?)</c>)', re.S)


def editar_fila(xml_fila, fila, valores):
    cabecera = re.match(r"<row[^>]*>", xml_fila).group(0)
    cuerpo = xml_fila[len(cabecera):-len("</row>")]
    celdas = {num_col(m.group(1)): m.group(0) for m in CELDA.finditer(cuerpo)}
    for col, (tipo, v) in valores.items():
        if v is None: continue
        estilo = re.search(r' s="(\d+)"', celdas.get(col, "") or "")
        s = estilo.group(1) if estilo else col_style.get(col, "0")
        ref = f"{letras_col(col)}{fila}"
        celdas[col] = f'<c r="{ref}" t="s" s="{s}"><v>{ss(v)}</v></c>' if tipo == "s" else f'<c r="{ref}" t="n" s="{s}"><v>{v}</v></c>'
    return cabecera + "".join(celdas[k] for k in sorted(celdas)) + "</row>"


fila = FILA_INICIAL
resumen = []
for g in grupos:
    at = g["at"]
    for v in g["variantes"]:
        valores = {
            2: ("s", g["titulo"]),                                          # MISMO título (de Pret) en todas las filas del producto
            5: ("s", v["gtin"]) if v["gtin"] else ("s", "El producto no tiene código registrado"),
            6: ("s", color_ml(v["color"])),
            7: ("s", ",".join(v["fotos"])),                                 # las fotos de ESTE color
            8: ("s", v["sku"]),
            9: ("n", 1),
            10: ("n", v["precio"]),
            11: ("s", at.get("SALE_FORMAT")) if at.get("SALE_FORMAT") in ("Unidad", "Pack") else None,
            12: ("n", int(at["UNITS_PER_PACK"])) if str(at.get("UNITS_PER_PACK", "")).isdigit() else None,
            13: ("s", g["desc"]),
            18: ("s", "A cargo del comprador"),
            19: ("s", "No acepto"),
            25: ("s", at.get("BRAND")),
            26: ("s", at.get("CURTAIN_AND_BLIND_TYPE")),
            27: ("n", g["alto"]), 28: ("s", g["u_alto"]),
            29: ("n", g["ancho"]), 30: ("s", g["u_ancho"]),
            31: ("s", at.get("IS_BLACK_OUT")),
            32: ("s", at.get("MODEL")),
            33: ("s", at.get("FABRIC_DESIGN")),
            34: ("s", at.get("MATERIAL")),
        }
        m = re.search(rf'<row r="{fila}"[^>]*>.*?</row>', hoja, re.S)
        assert m, f"no existe la fila {fila}"
        hoja = hoja[:m.start()] + editar_fila(m.group(0), fila, valores) + hoja[m.end():]
        resumen.append((fila, g["titulo"][:42], v["sku"], v["color"], len(v["fotos"]), v["precio"], v["gtin"] or "-", v["ofrecido"]))
        fila += 1

sst = sst.replace("</sst>", "".join(nuevas_si) + "</sst>")
sst = re.sub(r'uniqueCount="\d+"', f'uniqueCount="{n_unico + len(nuevas_si)}"', sst, count=1)
sst = re.sub(r'(<sst[^>]*\s)count="\d+"', rf'\g<1>count="{n_cuenta + len(nuevas_si)}"', sst, count=1)
partes[HOJA] = hoja.encode("utf-8")
partes["xl/sharedStrings.xml"] = sst.encode("utf-8")
with zipfile.ZipFile(SALIDA, "w", zipfile.ZIP_DEFLATED) as out:
    for n in orden: out.writestr(n, partes[n])

print(f"Planilla generada: {SALIDA} | publicaciones de Pret: {len(grupos)} | filas (colores): {len(resumen)}")
for g in grupos:
    at = g["at"]
    print(f"\n{g['mla']} -> título: {g['titulo']!r}")
    print("  ficha:", {k: at.get(k) for k in ("BRAND", "CURTAIN_AND_BLIND_TYPE", "MODEL", "MATERIAL", "FABRIC_DESIGN", "IS_BLACK_OUT", "SALE_FORMAT", "UNITS_PER_PACK")},
          "| alto", g["alto"], g["u_alto"], "| ancho", g["ancho"], g["u_ancho"])
print("\nfila | título | SKU | color | fotos | precio | GTIN | stock ofrecido")
for r in resumen: print(" | ".join(str(x) for x in r))
