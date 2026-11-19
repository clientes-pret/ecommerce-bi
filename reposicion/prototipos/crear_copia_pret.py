"""Prueba REAL (crea publicaciones, autorizado por el usuario el 18/11/2026): copia N colores de una publicación de Pret a la
cuenta de un revendedor, con el modelo nuevo de ML (una publicación por color, mismo family_name). Valida cada una antes de
crearla, le carga la descripción y la PAUSA enseguida (también si algo falla en el medio).
Uso: python3 crear_copia_pret.py MLA_DE_PRET ml_user_id_revendedor SKU [SKU ...]"""
import json, os, sys, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fotos_marca import sin_fotos_de_marca  # nunca copiar "¿Quiénes somos?" / "Preguntas frecuentes"

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
cfg = json.load(open(f"{ROOT}/config.json")); sb = cfg["supabase"]
H = {"apikey": sb["service_role_key"], "Authorization": "Bearer " + sb["service_role_key"]}
MLA, REV, SKUS = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
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
        r = urllib.request.urlopen(urllib.request.Request("https://api.mercadolibre.com" + path, headers=h, data=data, method=method), timeout=60)
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, raw[:300].decode("utf-8", "replace")


s, it = ml("GET", f"/items/{MLA}?include_attributes=all", TOK_PRET); assert s == 200, (s, it)
s, d = ml("GET", f"/items/{MLA}/description", TOK_PRET)
descripcion = (d.get("plain_text") or "").strip() if isinstance(d, dict) else ""
pics = {p["id"]: (p.get("secure_url") or p.get("url")) for p in it.get("pictures", [])}
NO_COPIAR = {"GIFTABLE", "IMPORT_DUTY", "VALUE_ADDED_TAX", "ITEM_CONDITION", "SELLER_SKU", "GTIN", "MAIN_COLOR", "PRODUCT_FEATURES",
             "SHIPMENT_PACKING", "SELLER_PACKAGE_HEIGHT", "SELLER_PACKAGE_LENGTH", "SELLER_PACKAGE_WEIGHT", "SELLER_PACKAGE_WIDTH"}
attrs_item = [{"id": a["id"], "value_name": a["value_name"], **({"value_id": a["value_id"]} if a.get("value_id") else {})}
              for a in it.get("attributes", []) if a.get("value_name") and a["id"] not in NO_COPIAR]

creadas = []
try:
    for sku in SKUS:
        v = next((x for x in it.get("variations", []) if any(a.get("id") == "SELLER_SKU" and a.get("value_name") == sku for a in x.get("attributes", []))), None)
        if not v:
            print(f"[!] {sku}: no está en {MLA}"); continue
        va = {a["id"]: a for a in v.get("attributes", []) if a.get("value_name")}
        attrs = list(attrs_item)
        attrs += [{"id": a["id"], "value_name": a["value_name"], **({"value_id": a["value_id"]} if a.get("value_id") else {})} for a in v.get("attribute_combinations", [])]
        attrs += [{"id": k, "value_name": va[k]["value_name"]} for k in ("SELLER_SKU", "GTIN") if k in va]
        fotos, fuera = sin_fotos_de_marca([i for i in v.get("picture_ids", []) if i in pics])
        if fuera: print(f"   {sku}: {len(fuera)} foto(s) de marca o sin revisar no se copian")
        if not fotos:
            print(f"[!] {sku}: no le queda ninguna foto propia; NO se crea"); continue
        payload = {
            "family_name": it["title"][:60], "category_id": it["category_id"], "currency_id": it["currency_id"],
            "price": round(float(v.get("price") or it["price"]), 2), "available_quantity": 1, "buying_mode": "buy_it_now",
            "condition": "new", "listing_type_id": "gold_special",
            "pictures": [{"source": pics[i]} for i in fotos][:10],
            "attributes": attrs, "seller_custom_field": sku,
            "shipping": {"mode": "me2", "local_pick_up": False, "free_shipping": False},
        }
        s, r = ml("POST", "/items/validate", TOK_REV, payload)
        errores = [c for c in ((r or {}).get("cause") or []) if c.get("type") == "error"] if isinstance(r, dict) else []
        if errores:
            print(f"[!] {sku}: la validación tiene errores; NO se crea:", [(c.get('code'), c.get('message')) for c in errores]); continue
        s, r = ml("POST", "/items", TOK_REV, payload)
        if s not in (200, 201):
            print(f"[!] {sku}: no se pudo crear: HTTP {s} {r}"); continue
        iid = r["id"]; creadas.append(iid)
        print(f"creada {iid} ({sku}) | título: {r.get('title')!r} | estado inicial: {r.get('status')}")
        if descripcion:
            s, r2 = ml("POST", f"/items/{iid}/description", TOK_REV, {"plain_text": descripcion})
            print(f"   descripción: HTTP {s}")
finally:
    for iid in creadas:
        for intento in range(3):
            s, r = ml("PUT", f"/items/{iid}", TOK_REV, {"status": "paused"})
            if s == 200: break
            time.sleep(2)
        print(f"PAUSA {iid}: HTTP {s} -> estado {(r or {}).get('status') if isinstance(r, dict) else r}")

print("\nCREADAS:", creadas)
