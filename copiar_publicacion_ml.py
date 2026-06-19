#!/usr/bin/env python3
"""
Copia la ÚLTIMA publicación de Pret a Home (ML) → Casa Lavan (ML)
Uso: python3 copiar_publicacion_ml.py [--publicar]
  Sin --publicar: solo muestra la info, no publica nada
  Con --publicar: crea la publicación en Casa Lavan
"""
import requests, json, sys, time

# ── Credenciales ─────────────────────────────────────────────────────────────
CLIENT_ID     = "1637574709714032"
CLIENT_SECRET = "7YEH6ppegi1GbKGu6DhYJptNDeWBjq1s"

PRET = {
    "user_id":       "1255615205",
    "refresh_token": "TG-69fb975967a2e700015b5b7e-1255615205",
}
LAVAN = {
    "user_id":       "189036603",
    "refresh_token": "TG-69fb97a421cc7f0001145244-189036603",
}

PUBLICAR = "--publicar" in sys.argv

# ── Helpers ──────────────────────────────────────────────────────────────────
def renovar_token(refresh_token):
    r = requests.post("https://api.mercadolibre.com/oauth/token", data={
        "grant_type":    "refresh_token",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
    })
    r.raise_for_status()
    data = r.json()
    return data["access_token"], data.get("refresh_token", refresh_token)

def headers(token):
    return {"Authorization": f"Bearer {token}"}

def get(url, token, **params):
    r = requests.get(url, headers=headers(token), params=params)
    r.raise_for_status()
    return r.json()

def post(url, token, body):
    r = requests.post(url, headers={**headers(token), "Content-Type": "application/json"},
                      json=body)
    return r

# ── 1. Renovar tokens ─────────────────────────────────────────────────────────
print("🔑 Renovando tokens...")
token_pret,  _ = renovar_token(PRET["refresh_token"])
token_lavan, _ = renovar_token(LAVAN["refresh_token"])
print("   ✓ Pret a Home")
print("   ✓ Casa Lavan\n")

# ── 2. Traer la última publicación de Pret ────────────────────────────────────
print("📦 Buscando última publicación de Pret a Home...")
search = get(
    f"https://api.mercadolibre.com/users/{PRET['user_id']}/items/search",
    token_pret,
    sort="start_time_desc",
    limit=1,
    offset=0,
)
item_ids = search.get("results", [])
if not item_ids:
    print("❌ No se encontraron publicaciones.")
    sys.exit(1)

item_id = item_ids[0]
print(f"   ID encontrado: {item_id}\n")

# Traer detalle completo
item = get(f"https://api.mercadolibre.com/items/{item_id}", token_pret)

print("=" * 60)
print(f"📋 PUBLICACIÓN ORIGINAL (Pret a Home)")
print("=" * 60)
print(f"  ID:          {item.get('id')}")
print(f"  Título:      {item.get('title')}")
print(f"  Categoría:   {item.get('category_id')}")
print(f"  Precio:      ${item.get('price'):,.2f} {item.get('currency_id')}")
print(f"  Tipo:        {item.get('listing_type_id')}")
print(f"  Estado:      {item.get('status')}")
print(f"  Stock:       {item.get('available_quantity')}")
print(f"  Condición:   {item.get('condition')}")
print(f"  URL:         {item.get('permalink')}")

variantes = item.get("variations", [])
print(f"  Variantes:   {len(variantes)}")
for v in variantes[:5]:
    attrs = {a["id"]: a.get("value_name") for a in v.get("attribute_combinations", [])}
    print(f"    - {attrs} | stock: {v.get('available_quantity')} | precio: {v.get('price')}")
if len(variantes) > 5:
    print(f"    ... y {len(variantes)-5} más")

fotos = item.get("pictures", [])
print(f"  Fotos:       {len(fotos)}")

atributos = item.get("attributes", [])
print(f"  Atributos:   {len(atributos)}")
print("=" * 60)

if not PUBLICAR:
    print("\n⚠️  MODO PREVIEW — no se publicó nada en Casa Lavan.")
    print("   Para publicar, corré con el flag --publicar:")
    print(f"   python3 {sys.argv[0]} --publicar\n")
    sys.exit(0)

# ── 3. Construir el cuerpo para Casa Lavan ────────────────────────────────────
print("\n🚀 Preparando publicación para Casa Lavan...")

# Subir las fotos a la cuenta de Casa Lavan
print(f"   Subiendo {len(fotos)} fotos a Casa Lavan...")
nuevas_fotos = []
for i, foto in enumerate(fotos):
    url_foto = foto.get("secure_url") or foto.get("url")
    r = requests.post(
        "https://api.mercadolibre.com/pictures",
        headers={**headers(token_lavan), "Content-Type": "application/json"},
        json={"source": url_foto}
    )
    if r.status_code in (200, 201):
        nuevas_fotos.append({"id": r.json()["id"]})
        print(f"   ✓ Foto {i+1}/{len(fotos)}")
    else:
        print(f"   ⚠ Foto {i+1} falló: {r.status_code} {r.text[:100]}")
    time.sleep(0.3)

# Construir variaciones limpias (sin IDs de la cuenta original)
variaciones_limpias = []
for v in variantes:
    nueva_var = {
        "attribute_combinations": v.get("attribute_combinations", []),
        "price": v.get("price"),
        "available_quantity": v.get("available_quantity", 0),
    }
    if v.get("picture_ids"):
        nueva_var["picture_ids"] = v.get("picture_ids")
    variaciones_limpias.append(nueva_var)

# Construir atributos limpios
atributos_limpios = [
    {"id": a["id"], "value_name": a.get("value_name")}
    for a in atributos
    if a.get("value_name")
]

cuerpo = {
    "title":              item["title"],
    "category_id":        item["category_id"],
    "price":              item["price"],
    "currency_id":        item.get("currency_id", "ARS"),
    "available_quantity": item.get("available_quantity", 1),
    "buying_mode":        item.get("buying_mode", "buy_it_now"),
    "condition":          item.get("condition", "new"),
    "listing_type_id":    item.get("listing_type_id", "gold_special"),
    "pictures":           nuevas_fotos if nuevas_fotos else [{"source": f["secure_url"]} for f in fotos[:12]],
    "attributes":         atributos_limpios,
}

if variaciones_limpias:
    cuerpo["variations"] = variaciones_limpias

# Agregar descripción si existe
desc_r = requests.get(
    f"https://api.mercadolibre.com/items/{item_id}/description",
    headers=headers(token_pret)
)
if desc_r.status_code == 200:
    cuerpo["description"] = {"plain_text": desc_r.json().get("plain_text", "")}

# ── 4. Publicar en Casa Lavan ─────────────────────────────────────────────────
print("\n📤 Publicando en Casa Lavan...")
resp = post("https://api.mercadolibre.com/items", token_lavan, cuerpo)

if resp.status_code in (200, 201):
    nuevo = resp.json()
    print("\n" + "=" * 60)
    print("✅ PUBLICACIÓN CREADA EN CASA LAVAN")
    print("=" * 60)
    print(f"  ID:    {nuevo.get('id')}")
    print(f"  Título: {nuevo.get('title')}")
    print(f"  URL:   {nuevo.get('permalink')}")
    print("=" * 60)
else:
    print(f"\n❌ Error al publicar: {resp.status_code}")
    try:
        err = resp.json()
        print(json.dumps(err, indent=2, ensure_ascii=False))
    except:
        print(resp.text[:500])
