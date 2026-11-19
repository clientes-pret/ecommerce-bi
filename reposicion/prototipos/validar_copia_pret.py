"""Prueba (NO crea nada): copia una publicación de Pret a Home al formato de publicación de un revendedor y la valida con
POST /items/validate en la cuenta del revendedor. Prueba las dos formas de variantes que puede aceptar ML:
  A) una publicación por color con el mismo family_name (modelo nuevo de ML, "user products");
  B) una sola publicación con el arreglo 'variations' (modelo clásico).
Uso: python3 validar_copia_pret.py MLA_DE_PRET [ml_user_id_revendedor]"""
import json, sys, urllib.request, urllib.error

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
cfg = json.load(open(f"{ROOT}/config.json")); sb = cfg["supabase"]
H = {"apikey": sb["service_role_key"], "Authorization": "Bearer " + sb["service_role_key"]}
MLA = sys.argv[1]
REV = int(sys.argv[2]) if len(sys.argv) > 2 else 14741685  # CASALAVAN
PRET = 1255615205


def db(p):
    return json.load(urllib.request.urlopen(urllib.request.Request(sb["url"] + "/rest/v1/" + p, headers=H), timeout=60))


TOK_PRET = db(f"rev_revendedores?ml_user_id=eq.{PRET}&select=access_token")[0]["access_token"]
TOK_REV = db(f"rev_revendedores?ml_user_id=eq.{REV}&select=access_token")[0]["access_token"]


def ml(method, path, tok, body=None):
    h = {"Authorization": "Bearer " + tok, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    try:
        r = urllib.request.urlopen(urllib.request.Request("https://api.mercadolibre.com" + path, headers=h, data=data, method=method), timeout=40)
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, raw[:300].decode("utf-8", "replace")


def mostrar(etiqueta, s, r):
    print(f"\n== {etiqueta}: HTTP {s}")
    if s == 204 or r is None:
        print("   ML ACEPTA el pedido."); return
    if isinstance(r, dict):
        print("   mensaje:", r.get("message"))
        for c in (r.get("cause") or [])[:15]:
            print("   -", c.get("type"), "|", c.get("code"), "|", (c.get("message") or "")[:200])
    else:
        print("  ", r)


s, it = ml("GET", f"/items/{MLA}?include_attributes=all", TOK_PRET)
assert s == 200, (s, it)
pics = {p["id"]: (p.get("secure_url") or p.get("url")) for p in it.get("pictures", [])}

# Atributos de la publicación que se copian (los que describen el producto). Se dejan afuera los que son propios de
# la cuenta de Pret o los calcula ML.
NO_COPIAR = {"GIFTABLE", "IMPORT_DUTY", "VALUE_ADDED_TAX", "ITEM_CONDITION", "SELLER_SKU", "GTIN", "MAIN_COLOR",
             # ML los ignora ("not modifiable"): los completa él o salen de la logística
             "PRODUCT_FEATURES", "SHIPMENT_PACKING", "SELLER_PACKAGE_HEIGHT", "SELLER_PACKAGE_LENGTH", "SELLER_PACKAGE_WEIGHT", "SELLER_PACKAGE_WIDTH"}
# Se manda siempre el texto (value_name) y, si existe, también el código: con solo el código ML no resolvió la marca.
attrs_item = [{"id": a["id"], "value_name": a["value_name"], **({"value_id": a["value_id"]} if a.get("value_id") else {})}
              for a in it.get("attributes", []) if a.get("value_name") and a["id"] not in NO_COPIAR]

colores = []
for v in it.get("variations", []):
    va = {a["id"]: a for a in v.get("attributes", []) if a.get("value_name")}
    if not va.get("SELLER_SKU"): continue
    of = db(f"rev_stock_ofrecido?sku=eq.{va['SELLER_SKU']['value_name']}&select=ofrecido")
    if not of or (of[0]["ofrecido"] or 0) < 1: continue
    colores.append(v)
    if len(colores) == 2: break
print(f"Publicación de Pret {MLA}: {it['title']!r} | {len(it.get('variations', []))} colores; se prueban {len(colores)} con stock ofrecido")

base = {
    "category_id": it["category_id"], "currency_id": it["currency_id"], "buying_mode": "buy_it_now", "condition": "new",
    "listing_type_id": "gold_special", "shipping": {"mode": "me2", "local_pick_up": False, "free_shipping": False},
}

# --- A) una publicación por color, mismo family_name
for v in colores:
    va = {a["id"]: a for a in v.get("attributes", []) if a.get("value_name")}
    comb = v.get("attribute_combinations", [])
    attrs = list(attrs_item)
    for a in comb:
        attrs.append({"id": a["id"], "value_name": a["value_name"], **({"value_id": a["value_id"]} if a.get("value_id") else {})})
    for k in ("SELLER_SKU", "GTIN"):
        if k in va: attrs.append({"id": k, "value_name": va[k]["value_name"]})
    payload = dict(base, family_name=it["title"][:60], price=round(float(v.get("price") or it["price"]), 2), available_quantity=1,
                   pictures=[{"source": pics[i]} for i in v.get("picture_ids", []) if i in pics][:10], attributes=attrs,
                   seller_custom_field=va["SELLER_SKU"]["value_name"])
    s, r = ml("POST", "/items/validate", TOK_REV, payload)
    mostrar(f"A) una publicación por color ({comb[0].get('value_name') if comb else '?'})", s, r)

# --- B) una sola publicación con variaciones
fotos_usadas = []
variaciones = []
for v in colores:
    va = {a["id"]: a for a in v.get("attributes", []) if a.get("value_name")}
    urls = [pics[i] for i in v.get("picture_ids", []) if i in pics][:6]
    fotos_usadas += [u for u in urls if u not in fotos_usadas]
    variaciones.append({
        "attribute_combinations": [{"id": a["id"], **({"value_id": a["value_id"]} if a.get("value_id") else {"value_name": a["value_name"]})} for a in v.get("attribute_combinations", [])],
        "price": round(float(v.get("price") or it["price"]), 2), "available_quantity": 1, "picture_ids": urls,
        "attributes": [{"id": k, "value_name": va[k]["value_name"]} for k in ("SELLER_SKU", "GTIN") if k in va],
    })
payload_b = dict(base, title=it["title"][:60], price=round(float(it["price"]), 2), available_quantity=len(variaciones),
                 pictures=[{"source": u} for u in fotos_usadas], attributes=attrs_item, variations=variaciones)
s, r = ml("POST", "/items/validate", TOK_REV, payload_b)
mostrar("B) una sola publicación con variaciones", s, r)
