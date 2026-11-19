"""Llena la planilla oficial de ML (cortinas manuales) con productos del catálogo, editando SOLO las celdas de datos de la hoja
'Manuales' dentro del .xlsx (se conserva todo lo demás: hoja oculta con el id de la cuenta, listas desplegables, fórmulas, imágenes).
Uso: python3 llenar_planilla_ml.py PLANTILLA.xlsx SALIDA.xlsx [N]"""
import json, re, sys, zipfile, html, unicodedata, urllib.request, urllib.parse, urllib.error, collections

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
cfg = json.load(open(f"{ROOT}/config.json")); sb = cfg["supabase"]
H = {"apikey": sb["service_role_key"], "Authorization": "Bearer " + sb["service_role_key"]}
PLANTILLA, SALIDA = sys.argv[1], sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 10
FILA_INICIAL = 8
HOJA = "xl/worksheets/sheet3.xml"  # 'Manuales'


def db(p, extra=None):
    h = dict(H); h.update(extra or {})
    return json.load(urllib.request.urlopen(urllib.request.Request(sb["url"] + "/rest/v1/" + p, headers=h), timeout=60))


def db_all(p, orden):
    out, off = [], 0
    while True:
        r = db(p + f"&order={orden}", {"Range": f"{off}-{off+999}"})
        out += r
        if len(r) < 1000: return out
        off += 1000


sin_acentos = lambda s: "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower().strip()

# ---------- 1. Candidatas: cortinas comunes, sin ambigüedad, de marcas conocidas, con stock ofrecido
cat = {c["sku"] for c in db_all("rev_catalogo?select=sku&habilitado=eq.true", "sku.asc")}
rp = {r["sku"]: r for r in db_all("repo_productos?select=sku,proveedor_auto,proveedor_manual,categoria_auto,categoria_manual", "sku.asc") if r["sku"] in cat}
info = {i["sku"]: i for i in db_all("rev_catalogo_info?select=sku,titulo,descripcion,fotos,precio_sugerido", "sku.asc")}
cc = {p["sku"]: p for p in db_all("cc_productos?select=sku,producto,color", "sku.asc")}
ofr = {o["sku"]: o["ofrecido"] for o in db_all("rev_stock_ofrecido?select=sku,ofrecido", "sku.asc")}

MARCA = {"Jean Cartier": "Jean Cartier", "Pret": "Pret a Home", "Palette": "Palette", "Alcoyana": "Alcoyana"}
MEDIDA = re.compile(r"(\d{2,3})\s*[xX×]\s*(\d{2,3})")
colores_ml = None  # se completa al leer la planilla
cands = []
for sku, r in rp.items():
    if (r["categoria_manual"] or r["categoria_auto"]) != "Cortinas": continue
    i = info.get(sku)
    if not i or not i["titulo"] or not i["precio_sugerido"]: continue
    t = i["titulo"]
    if re.search(r"baño|bano|ganchos|set de", t, re.I): continue
    prov = r["proveedor_manual"] or r["proveedor_auto"] or ""
    if prov not in MARCA: continue
    fotos = [f for f in (i["fotos"] or []) if f]
    if len(fotos) < 3 or (ofr.get(sku) or 0) < 3: continue
    m = MEDIDA.search(t) or MEDIDA.search(sku)
    if not m: continue
    a, b = int(m.group(1)), int(m.group(2))
    if a > b: continue  # orden dudoso: la prueba solo usa las claras (ancho menor que alto)
    c = cc.get(sku) or {}
    color = c.get("color") or (t.split(" - ")[-1] if " - " in t else None)
    if not color or len(color) > 25: continue
    tl = t.lower()
    tipo = "Visillo" if ("voile" in tl or "visillo" in tl) else "Roller" if "roller" in tl else "Romana" if "romana" in tl else "Americana" if "americana" in tl else "Panel"
    blackout = "Sí" if re.search(r"black\s*out|blackout", tl) else "No"
    base = c.get("producto") or t.split(" - ")[0]
    cands.append(dict(sku=sku, titulo=t, base=base, marca=MARCA[prov], ancho=a, alto=b, color=color, tipo=tipo, blackout=blackout,
                      precio=int(i["precio_sugerido"]), fotos=fotos[:6], desc=i["descripcion"] or "", stock_ofrecido=ofr[sku]))
print(f"Candidatas claras: {len(cands)}")

# Se eligen N variadas: una por familia de producto primero
elegidas, familias = [], collections.Counter()
for c in sorted(cands, key=lambda x: (familias[x["base"]], x["sku"])):
    if len(elegidas) >= N: break
    if familias[c["base"]] >= 3: continue
    elegidas.append(c); familias[c["base"]] += 1
elegidas = elegidas[:N]

# ---------- 2. Código de barras desde Tiendanube (solo lectura)
tn = cfg["channels"]["tn_pret"]
def barcode(sku):
    try:
        r = urllib.request.Request(f"https://api.tiendanube.com/v1/{tn['store_id']}/products/sku/{urllib.parse.quote(sku)}",
                                   headers={"Authentication": "bearer " + tn["access_token"], "User-Agent": "Planilla (soporte)"})
        p = json.load(urllib.request.urlopen(r, timeout=30))
        for v in p.get("variants", []):
            if v.get("sku") == sku and re.fullmatch(r"\d{8,14}", (v.get("barcode") or "").strip()): return v["barcode"].strip()
    except Exception: pass
    return None
for c in elegidas: c["gtin"] = barcode(c["sku"])

# ---------- 3. Textos
def limpiar_desc(s):
    s = re.sub(r"<\s*br\s*/?>|</p>|</li>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", s)).strip()[:4000]

def titulo_ml(c):
    t = c["titulo"]
    if len(t) <= 60: return t
    base = f"{c['base']} {c['ancho']}x{c['alto']} Cm - {c['color']}"
    return base[:60].rstrip(" -")

# ---------- 4. Edición del xlsx
z = zipfile.ZipFile(PLANTILLA)
partes = {n: z.read(n) for n in z.namelist()}
orden = [i.filename for i in z.infolist()]
hoja = partes[HOJA].decode("utf-8")
sst = partes["xl/sharedStrings.xml"].decode("utf-8")

# listas permitidas (hoja oculta 'extra info' = sheet2) para ajustar el color a la lista de ML cuando coincide
ex = partes["xl/worksheets/sheet2.xml"].decode("utf-8")
m_unique = re.search(r'uniqueCount="(\d+)"', sst); m_count = re.search(r'<sst[^>]*\scount="(\d+)"', sst)
n_unico = int(m_unique.group(1)); n_cuenta = int(m_count.group(1))
nuevas_si = []
def ss(texto):
    """Agrega un texto a las cadenas compartidas y devuelve su índice."""
    nuevas_si.append(f'<si><t xml:space="preserve">{html.escape(texto, quote=False)}</t></si>')
    return n_unico + len(nuevas_si) - 1

# colores válidos de ML: están como texto en la hoja oculta; se toman de las cadenas compartidas
todas_si = re.findall(r"<si>(.*?)</si>", sst, re.S)
def texto_si(x): return html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", x, re.S)))
lista_colores = {}
try:
    i0 = [texto_si(x) for x in todas_si]
    k = i0.index("Coral claro")
    for t in i0[k:k + 51]: lista_colores[sin_acentos(t)] = t
except ValueError:
    pass

def color_ml(c):
    return lista_colores.get(sin_acentos(c), c)

# estilos por defecto de cada columna (para celdas que no existen todavía en la fila)
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
    """valores: {columna(int): ('s', texto) | ('n', numero)}. Reemplaza/inserta esas celdas y deja todas las demás intactas."""
    cabecera = re.match(r"<row[^>]*>", xml_fila).group(0)
    cuerpo = xml_fila[len(cabecera):-len("</row>")]
    celdas = {}
    for m in CELDA.finditer(cuerpo):
        celdas[num_col(m.group(1))] = m.group(0)
    for col, (tipo, v) in valores.items():
        estilo = re.search(r' s="(\d+)"', celdas.get(col, "") or "")
        s = estilo.group(1) if estilo else col_style.get(col, "0")
        ref = f"{letras_col(col)}{fila}"
        if tipo == "s": celdas[col] = f'<c r="{ref}" t="s" s="{s}"><v>{ss(v)}</v></c>'
        else: celdas[col] = f'<c r="{ref}" t="n" s="{s}"><v>{v}</v></c>'
    return cabecera + "".join(celdas[k] for k in sorted(celdas)) + "</row>"

resumen = []
for k, c in enumerate(elegidas):
    fila = FILA_INICIAL + k
    gtin = c["gtin"]
    valores = {
        2: ("s", titulo_ml(c)),
        5: ("s", gtin) if gtin else ("s", "El producto no tiene código registrado"),
        6: ("s", color_ml(c["color"])),
        7: ("s", ",".join(c["fotos"])),
        8: ("s", c["sku"]),
        9: ("n", 1),
        10: ("n", c["precio"]),
        13: ("s", limpiar_desc(c["desc"])),
        18: ("s", "A cargo del comprador"),
        19: ("s", "No acepto"),
        25: ("s", c["marca"]),
        26: ("s", c["tipo"]),
        27: ("n", c["alto"]), 28: ("s", "cm"),
        29: ("n", c["ancho"]), 30: ("s", "cm"),
        31: ("s", c["blackout"]),
    }
    m = re.search(rf'<row r="{fila}"[^>]*>.*?</row>', hoja, re.S)
    assert m, f"no existe la fila {fila}"
    hoja = hoja[:m.start()] + editar_fila(m.group(0), fila, valores) + hoja[m.end():]
    resumen.append((fila, c["sku"], titulo_ml(c), c["marca"], f'{c["ancho"]}x{c["alto"]}', c["color"], c["tipo"], c["blackout"], c["precio"], gtin or "-", len(c["fotos"])))

sst = sst.replace("</sst>", "".join(nuevas_si) + "</sst>")
sst = re.sub(r'uniqueCount="\d+"', f'uniqueCount="{n_unico + len(nuevas_si)}"', sst, count=1)
sst = re.sub(r'(<sst[^>]*\s)count="\d+"', rf'\g<1>count="{n_cuenta + len(nuevas_si)}"', sst, count=1)
partes[HOJA] = hoja.encode("utf-8")
partes["xl/sharedStrings.xml"] = sst.encode("utf-8")

with zipfile.ZipFile(SALIDA, "w", zipfile.ZIP_DEFLATED) as out:
    for n in orden: out.writestr(n, partes[n])

print(f"\nPlanilla generada: {SALIDA}")
print("fila | SKU | título | marca | ancho x alto | color | tipo | blackout | precio | GTIN | fotos")
for r in resumen: print(" | ".join(str(x) for x in r))
