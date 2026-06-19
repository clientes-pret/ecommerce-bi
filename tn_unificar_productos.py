"""
tn_unificar_productos.py
Unifica productos de Tienda Nube separados por plaza/tamaño en un único producto
con dos propiedades: Plazas + Color.

Uso:
  # Modo DRY RUN (solo muestra qué haría, no toca nada):
  python3 tn_unificar_productos.py --dry-run

  # Procesar UN producto específico (recomendado para probar):
  python3 tn_unificar_productos.py --handle cover-cairo-palette-twin cover-cairo-palette-queen

  # Procesar todos los productos detectados automáticamente:
  python3 tn_unificar_productos.py --all

Requiere: pip install requests
"""

import requests
import json
import time
import re
import argparse
from collections import defaultdict

# ── Credenciales ──────────────────────────────────────────────────────────────
STORE_ID    = '2625285'
TOKEN       = '7bf4cde46764d96772079d8cb1d10cd644aa35a0'
HEADERS     = {
    'Authentication': f'bearer {TOKEN}',
    'User-Agent':     'PretAHome-Tools (matias@pretahome.com)',
    'Content-Type':   'application/json'
}
BASE_URL    = f'https://api.tiendanube.com/v1/{STORE_ID}'

# ── Palabras clave de tamaño para detectar productos a unificar ───────────────
SIZE_KEYWORDS = [
    'twin', 'queen', 'king', 'super-king', 'super-twin',
    '1-plaza', '2-plaza', '3-plaza',
    '1-1-2-plaza', '2-1-2-plaza', '2-1-2-plazas',
    'matrimonial'
]

# Mapeo de handle → nombre de plaza legible
PLAZA_MAP = {
    '1-plaza':        '1 Plaza',
    '1-1-2-plaza':    '1 1/2 Plaza',
    '2-plaza':        '2 Plazas',
    '2-1-2-plaza':    '2 1/2 Plazas',
    '2-1-2-plazas':   '2 1/2 Plazas',
    '3-plaza':        '3 Plazas',
    'twin':           'Twin',
    'queen':          'Queen',
    'king':           'King',
    'super-king':     'Super King',
    'super-twin':     'Super Twin',
    'matrimonial':    'Matrimonial',
}


# ── Helpers de API ─────────────────────────────────────────────────────────────

def api_get(path, params=None):
    time.sleep(0.5)
    r = requests.get(f'{BASE_URL}{path}', headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()

def api_put(path, payload):
    time.sleep(0.5)
    r = requests.put(f'{BASE_URL}{path}', headers=HEADERS, json=payload)
    if not r.ok:
        print(f'  ❌ PUT {path} → {r.status_code}')
        print(f'  Payload: {json.dumps(payload, indent=2, ensure_ascii=False)[:800]}')
        print(f'  Respuesta: {r.text[:800]}')
        r.raise_for_status()
    return r.json()

def api_post(path, payload):
    time.sleep(0.5)
    r = requests.post(f'{BASE_URL}{path}', headers=HEADERS, json=payload)
    if not r.ok:
        print(f'  ❌ POST {path} → {r.status_code}')
        print(f'  Payload: {json.dumps(payload, indent=2, ensure_ascii=False)[:800]}')
        print(f'  Respuesta: {r.text[:800]}')
        r.raise_for_status()
    return r.json()

def api_delete(path):
    time.sleep(0.5)
    r = requests.delete(f'{BASE_URL}{path}', headers=HEADERS)
    r.raise_for_status()
    return r.status_code


# ── Obtener todos los productos (paginado) ─────────────────────────────────────

def get_all_products():
    products = []
    page = 1
    while True:
        batch = api_get('/products', params={
            'per_page': 200,
            'page': page,
            'fields': 'id,name,handle,variants,categories,description,tags,seo_title,seo_description,brand,requires_shipping,attributes'
        })
        if not batch:
            break
        products.extend(batch)
        print(f'  Cargados {len(products)} productos...', end='\r')
        if len(batch) < 200:
            break
        page += 1
    print()
    return products


# ── Detectar grupos de productos a unificar ────────────────────────────────────

def extract_size_from_handle(handle):
    """Devuelve (base_handle, plaza_key) o (handle, None) si no tiene tamaño."""
    for kw in sorted(SIZE_KEYWORDS, key=len, reverse=True):  # longest match first
        pattern = rf'(?:^|-){re.escape(kw)}(?:-|$)'
        if re.search(pattern, handle):
            base = re.sub(rf'(?:^|-){re.escape(kw)}(?:-|$)', '-', handle).strip('-')
            base = re.sub(r'-+', '-', base)
            return base, kw
    return handle, None

def group_products_by_base(products):
    """Agrupa productos que comparten el mismo handle base."""
    groups = defaultdict(list)
    for p in products:
        handle = p['handle'].get('es', p['handle'].get('pt', list(p['handle'].values())[0]))
        base, size_kw = extract_size_from_handle(handle)
        if size_kw:
            groups[base].append({'product': p, 'handle': handle, 'size_kw': size_kw})
    # Solo grupos con 2+ productos
    return {k: v for k, v in groups.items() if len(v) >= 2}


# ── Lógica de unificación ──────────────────────────────────────────────────────

def get_existing_color_prop(product):
    """Devuelve el nombre de la propiedad de color si existe."""
    for attr in product.get('attributes', []):
        name = attr.get('es', '').lower()
        if 'color' in name or 'colour' in name:
            return attr.get('es', 'Color')
    return 'Color'

def merge_categories(products_list):
    """Une las categorías de todos los productos sin duplicar. TN espera lista de enteros."""
    seen_ids = set()
    merged = []
    for item in products_list:
        for cat in item['product'].get('categories', []):
            if cat['id'] not in seen_ids:
                seen_ids.add(cat['id'])
                merged.append(cat['id'])  # TN espera int, no {"id": int}
    return merged

def build_unified_variants(items_with_sizes):
    """
    Construye la lista de variantes unificadas con Plazas + Color.
    items_with_sizes: lista de {'product': ..., 'size_kw': ..., 'handle': ...}
    """
    variants = []
    for item in items_with_sizes:
        product  = item['product']
        size_kw  = item['size_kw']
        plaza    = PLAZA_MAP.get(size_kw, size_kw.replace('-', ' ').title())

        for v in product.get('variants', []):
            # Obtener color actual de la variante
            color = None
            for val in v.get('values', []):
                color = val.get('es') or val.get('pt') or list(val.values())[0]
                break

            variant = {
                'price':          v.get('price'),
                'promotional_price': v.get('promotional_price'),
                'stock_management': v.get('stock_management', True),
                'stock':          v.get('stock', 0),
                'sku':            v.get('sku', ''),
                'barcode':        v.get('barcode', ''),
                'weight':         v.get('weight'),
                'width':          v.get('width'),
                'height':         v.get('height'),
                'depth':          v.get('depth'),
                'values': [
                    {'es': plaza},   # Propiedad 1: Plazas
                ]
            }
            if color:
                variant['values'].append({'es': color})  # Propiedad 2: Color

            variants.append(variant)

    return variants

def unify_product_group(base_handle, items, dry_run=True):
    """
    Toma el grupo de productos y los unifica en uno solo.
    - Usa el primer producto como base (el que tiene handle más corto o el primero)
    - Actualiza sus variantes y handle
    - Elimina los demás
    """
    # Ordenar: primero el de handle más corto (suele ser el más limpio)
    items_sorted = sorted(items, key=lambda x: len(x['handle']))
    base_item    = items_sorted[0]
    base_product = base_item['product']
    base_id      = base_product['id']

    print(f'\n{"─"*60}')
    print(f'BASE:    {base_item["handle"]} (ID: {base_id})')
    for it in items_sorted[1:]:
        print(f'FUSIONAR: {it["handle"]} (ID: {it["product"]["id"]})')

    # Nuevo handle sin tamaño
    new_handle = base_handle
    print(f'NUEVO HANDLE: {new_handle}')

    # Categorías unificadas
    merged_cats = merge_categories(items_sorted)
    print(f'CATEGORÍAS: {len(merged_cats)} categorías unificadas')

    # Variantes unificadas
    unified_variants = build_unified_variants(items_sorted)
    print(f'VARIANTES: {len(unified_variants)} variantes ({len(items_sorted)} plazas)')
    for v in unified_variants:
        vals = ' / '.join(x.get('es','?') for x in v['values'])
        print(f'  {vals} — SKU: {v.get("sku","—")} — Precio: {v.get("price","?")} — Stock: {v.get("stock","?")}')

    # Atributos: Plazas + Color
    color_prop = get_existing_color_prop(base_product)
    new_attributes = [
        {'es': 'Plazas'},
        {'es': color_prop}
    ]
    print(f'ATRIBUTOS: {[a["es"] for a in new_attributes]}')

    if dry_run:
        print('  ⚠️  DRY RUN — no se realizaron cambios')
        return

    # 1. Actualizar producto base: handle + categorías + atributos
    print(f'  PUT /products/{base_id} → actualizando handle y categorías...')
    api_put(f'/products/{base_id}', {
        'handle':     {'es': new_handle},
        'categories': merged_cats,
        'attributes': new_attributes,
    })

    # 2. Eliminar variantes existentes del producto base
    print(f'  Eliminando variantes actuales...')
    for v in base_product.get('variants', []):
        try:
            api_delete(f'/products/{base_id}/variants/{v["id"]}')
        except Exception as e:
            print(f'    ⚠️  No se pudo eliminar variante {v["id"]}: {e}')

    # 3. Crear variantes unificadas
    print(f'  Creando {len(unified_variants)} variantes unificadas...')
    for v in unified_variants:
        try:
            api_post(f'/products/{base_id}/variants', v)
        except Exception as e:
            print(f'    ⚠️  Error creando variante {v.get("sku")}: {e}')

    # 4. Eliminar productos secundarios (TN genera redirect 301 automático)
    for item in items_sorted[1:]:
        sec_id = item['product']['id']
        print(f'  DELETE /products/{sec_id} ({item["handle"]}) → TN genera redirect 301...')
        try:
            api_delete(f'/products/{sec_id}')
            print(f'    ✅ Eliminado. Redirect 301: /{item["handle"]} → /{new_handle}')
        except Exception as e:
            print(f'    ⚠️  Error eliminando {sec_id}: {e}')

    print(f'  ✅ Unificación completa: /{new_handle}')


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Unifica productos TN por plaza/tamaño')
    parser.add_argument('--dry-run', action='store_true', help='Solo muestra qué haría, sin cambios')
    parser.add_argument('--all',     action='store_true', help='Procesa todos los grupos detectados')
    parser.add_argument('--handle',  nargs='+',           help='Handles específicos a unificar (ej: cover-cairo-palette-twin cover-cairo-palette-queen)')
    args = parser.parse_args()

    if args.handle:
        # Modo manual: handles específicos pasados por argumento
        print(f'Cargando {len(args.handle)} productos específicos...')
        products_to_process = []
        for h in args.handle:
            results = api_get('/products', params={'q': h.replace('-', ' '), 'fields': 'id,name,handle,variants,categories,description,tags,seo_title,seo_description,brand,requires_shipping,attributes'})
            # Buscar match exacto de handle
            for p in results:
                p_handle = p['handle'].get('es', p['handle'].get('pt', ''))
                if p_handle == h:
                    products_to_process.append(p)
                    print(f'  ✅ Encontrado: {h} (ID: {p["id"]})')
                    break
            else:
                print(f'  ⚠️  No encontrado: {h}')

        if len(products_to_process) < 2:
            print('Se necesitan al menos 2 productos para unificar.')
            return

        # Agrupar estos productos específicos
        items = []
        for p in products_to_process:
            handle = p['handle'].get('es', '')
            _, size_kw = extract_size_from_handle(handle)
            if not size_kw:
                print(f'  ⚠️  No se detectó tamaño en el handle: {handle}')
                size_kw = 'unknown'
            items.append({'product': p, 'handle': handle, 'size_kw': size_kw})

        # Determinar base handle
        base_handle, _ = extract_size_from_handle(items[0]['handle'])

        unify_product_group(base_handle, items, dry_run=args.dry_run)

    elif args.all or args.dry_run:
        print('Cargando todos los productos...')
        all_products = get_all_products()
        print(f'Total productos: {len(all_products)}')

        groups = group_products_by_base(all_products)
        print(f'Grupos detectados para unificar: {len(groups)}')

        for base_handle, items in groups.items():
            unify_product_group(base_handle, items, dry_run=args.dry_run)

    else:
        print('Especificá --dry-run, --all, o --handle <handle1> <handle2> ...')
        print('Ejemplos:')
        print('  python3 tn_unificar_productos.py --dry-run')
        print('  python3 tn_unificar_productos.py --handle cover-cairo-palette-twin cover-cairo-palette-queen')
        print('  python3 tn_unificar_productos.py --all')

if __name__ == '__main__':
    main()
