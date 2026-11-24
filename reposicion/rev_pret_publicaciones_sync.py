"""Copia a `rev_pret_publicaciones` las publicaciones ACTIVAS de Pret a Home en Mercado Libre, una fila por publicación y
variante con su SKU, separando TRADICIONALES de CATÁLOGO (18/11/2026). El portal de revendedores la usa para ofrecer, por
producto, "publicación tradicional" (tal cual o con la marca del revendedor si es marca Pret) y "sumarse al catálogo".

Solo LEE Mercado Libre, con la conexión propia de este sistema a la cuenta de Pret (fila 'pausado' de rev_revendedores,
ml_user_id 1255615205), que es independiente de la que usa armado-api: si venció se renueva y se guarda en esa misma fila.
Escribe solo en rev_pret_publicaciones: reemplaza la foto completa, y borra lo que ya no está activo SOLO si se leyó al
menos el 95 % de las publicaciones (si ML falla a medias no se pierde nada).

Guarda también `creada_at` (date_created del item: cuándo Pret creó la publicación), para el filtro "nuevas de Pret" del
catálogo (24/11/2026). Corre solo con .github/workflows/rev_pret_publicaciones.yml.

Uso: python3 -m reposicion.rev_pret_publicaciones_sync [--seco]"""
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRET = 1255615205
ML = "https://api.mercadolibre.com"
UA = "PretaHome-Revendedores/1.0"
ATRIBUTOS = "id,status,title,price,permalink,sold_quantity,catalog_listing,catalog_product_id,seller_custom_field,attributes,variations,date_created"

sys.path.insert(0, str(ROOT))
from reposicion.db import load_config  # noqa: E402  (CONFIG_JSON en GitHub, config.json en la compu; con los secrets que pisan claves)

cfg = load_config()
SB = cfg["supabase"]
H_SB = {"apikey": SB["service_role_key"], "Authorization": "Bearer " + SB["service_role_key"], "Content-Type": "application/json"}


def sb(method, path, body=None, extra=None):
    req = urllib.request.Request(SB["url"] + "/rest/v1/" + path, method=method, headers=dict(H_SB, **(extra or {})),
                                 data=json.dumps(body).encode() if body is not None else None)
    r = urllib.request.urlopen(req, timeout=120)
    raw = r.read()
    return json.loads(raw) if raw else None


def token_pret():
    fila = sb("GET", f"rev_revendedores?ml_user_id=eq.{PRET}&select=id,estado,access_token,refresh_token,token_expires_at")[0]
    assert fila["estado"] == "pausado", "la cuenta de Pret tiene que estar 'pausado' (solo lectura)"
    vence = dt.datetime.fromisoformat(fila["token_expires_at"].replace("Z", "+00:00"))
    if vence - dt.datetime.now(dt.timezone.utc) > dt.timedelta(minutes=10):
        return fila["access_token"]
    app = cfg["channels"]["ml_pret"]  # misma app (4032) con la que se conectó la fila; su cadena de acceso es solo nuestra
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": str(app["client_id"]),
                                   "client_secret": app["client_secret"], "refresh_token": fila["refresh_token"]}).encode()
    t = json.load(urllib.request.urlopen(urllib.request.Request(ML + "/oauth/token", data=body, headers={
        "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}), timeout=60))
    assert t["user_id"] == PRET
    ahora = dt.datetime.now(dt.timezone.utc)
    sb("PATCH", f"rev_revendedores?id=eq.{fila['id']}", {
        "access_token": t["access_token"], "refresh_token": t["refresh_token"], "actualizado_at": ahora.isoformat(),
        "token_expires_at": (ahora + dt.timedelta(seconds=t.get("expires_in", 21600))).isoformat()})
    print("acceso de Pret renovado")
    return t["access_token"]


def ml(path, tok):
    for intento in range(4):
        try:
            req = urllib.request.Request(ML + path, headers={"Authorization": "Bearer " + tok, "User-Agent": UA})
            return json.load(urllib.request.urlopen(req, timeout=60))
        except urllib.error.HTTPError as e:
            if e.code in (401, 404) or intento == 3:
                raise
            time.sleep(2 * (intento + 1))


def sku_de(attrs, scf=None):
    v = next((a.get("value_name") for a in attrs or [] if a.get("id") == "SELLER_SKU" and a.get("value_name")), None)
    return (v or scf or "").strip() or None


def filas_de_item(it, ahora):
    """Una fila por variante con SKU (o una por la publicación si no tiene variantes)."""
    tipo = "catalogo" if it.get("catalog_listing") else "tradicional"
    marca = next((a.get("value_name") for a in it.get("attributes") or [] if a.get("id") == "BRAND"), None)
    base = {"ml_item_id": it["id"], "tipo": tipo, "catalog_product_id": it.get("catalog_product_id") if tipo == "catalogo" else None,
            "marca": marca, "titulo": it.get("title"), "vendidas": it.get("sold_quantity"), "permalink": it.get("permalink"),
            "visto_at": ahora, "creada_at": it.get("date_created")}
    out = []
    for v in it.get("variations") or []:
        s = sku_de(v.get("attributes"), v.get("seller_custom_field"))
        if s:
            out.append(dict(base, ml_variation_id=v["id"], sku=s, precio=v.get("price") or it.get("price")))
    if not it.get("variations"):
        s = sku_de(it.get("attributes"), it.get("seller_custom_field"))
        if s:
            out.append(dict(base, ml_variation_id=0, sku=s, precio=it.get("price")))
    return out


def main(seco=False):
    tok = token_pret()
    ids, scroll = [], None
    while True:
        r = ml(f"/users/{PRET}/items/search?status=active&search_type=scan&limit=100" + (f"&scroll_id={scroll}" if scroll else ""), tok)
        if not r.get("results"):
            break
        ids += r["results"]
        scroll = r.get("scroll_id")
    ids = list(dict.fromkeys(ids))
    ahora = dt.datetime.now(dt.timezone.utc).isoformat()
    items = []
    for i in range(0, len(ids), 20):
        lote = ids[i:i + 20]
        try:
            res = ml("/items?ids=" + ",".join(lote) + "&include_attributes=all&attributes=" + ATRIBUTOS, tok)
            items += [x["body"] for x in res if x.get("code") == 200]
        except urllib.error.HTTPError:  # el multiget a veces da 403: de a una
            for one in lote:
                try:
                    items.append(ml(f"/items/{one}?include_attributes=all", tok))
                except urllib.error.HTTPError:
                    pass
    filas = {}
    for it in items:
        if it.get("status") != "active":
            continue
        for f in filas_de_item(it, ahora):
            filas[(f["ml_item_id"], f["ml_variation_id"])] = f
    filas = list(filas.values())
    tipos = {t: sum(1 for f in filas if f["tipo"] == t) for t in ("tradicional", "catalogo")}
    print(f"publicaciones activas: {len(ids)} | leídas: {len(items)} | filas con SKU: {len(filas)} {tipos} | SKUs distintos: {len({f['sku'] for f in filas})}")
    if seco:
        print("(--seco: no se escribe nada)")
        return
    for i in range(0, len(filas), 500):
        sb("POST", "rev_pret_publicaciones?on_conflict=ml_item_id,ml_variation_id", filas[i:i + 500],
           {"Prefer": "resolution=merge-duplicates,return=minimal"})
    if ids and len(items) >= 0.95 * len(ids):
        sb("DELETE", "rev_pret_publicaciones?visto_at=lt." + urllib.parse.quote(ahora), extra={"Prefer": "return=minimal"})
        print("guardado; se quitaron las que ya no están activas")
    else:
        print(f"guardado; NO se borra nada (se leyó {len(items)} de {len(ids)})")


if __name__ == "__main__":
    main(seco="--seco" in sys.argv)
