"""Prueba corta del token de Meta (solo lectura): ¿lo acepta la cuenta publicitaria de Pret y qué datos devuelve?

Lee META_ADS_TOKEN del entorno (secret de GitHub). Nunca imprime el token: el token viaja en el encabezado
Authorization y los errores se muestran solo con el cuerpo que devuelve Meta.
Cuenta de Pret: «Preta Prueba MP» (1647230420018605). Uso: python3 -m reposicion.meta_probe
"""
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

CUENTA = "act_1647230420018605"
API = "https://graph.facebook.com/v21.0"


def pedir(ruta, params=None):
    token = os.environ.get("META_ADS_TOKEN", "").strip()
    if not token:
        sys.exit("Falta el secret META_ADS_TOKEN en el entorno.")
    url = f"{API}/{ruta}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "replace")[:600]
        return {"_error": e.code, "_cuerpo": cuerpo}


def main():
    hoy = dt.datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date()
    desde, hasta = hoy - dt.timedelta(days=7), hoy - dt.timedelta(days=1)
    rango = json.dumps({"since": desde.isoformat(), "until": hasta.isoformat()})
    print(f"Prueba del token de Meta · período {desde} a {hasta}\n")

    r = pedir("me", {"fields": "id,name"})
    print("1) ¿Quién es el token?", r if "_error" in r else f"usuario del sistema «{r.get('name')}»")

    r = pedir(CUENTA, {"fields": "name,currency,account_status,timezone_name"})
    print("2) Cuenta publicitaria:", r)

    r = pedir(f"{CUENTA}/insights", {"level": "account", "time_range": rango, "time_increment": 1, "fields": "spend,impressions,clicks"})
    if "_error" in r:
        print("3) Gasto por día:", r)
    else:
        filas = r.get("data", [])
        total = sum(float(x.get("spend", 0)) for x in filas)
        print(f"3) Gasto por día: {len(filas)} días, total {total:,.0f} ({', '.join(x['date_start'][5:] + ' ' + format(float(x['spend']), ',.0f') for x in filas)})")

    r = pedir(f"{CUENTA}/insights", {"level": "ad", "time_range": rango, "fields": "ad_id,ad_name,campaign_name,spend,actions", "limit": 500})
    if "_error" in r:
        print("4) Gasto por anuncio:", r)
    else:
        filas = r.get("data", [])
        print(f"4) Gasto por anuncio: {len(filas)} anuncios con gasto en el período (primera página)")
        for x in sorted(filas, key=lambda x: -float(x.get("spend", 0)))[:5]:
            print(f"   {float(x['spend']):>10,.0f}  {x.get('ad_id')}  {x.get('campaign_name', '')[:50]}")

    r = pedir(f"{CUENTA}/insights", {"level": "ad", "time_range": rango, "breakdowns": "product_id", "fields": "ad_id,spend,impressions,clicks", "limit": 25})
    if "_error" in r:
        print("5) Desglose por producto del catálogo:", r)
    else:
        filas = r.get("data", [])
        print(f"5) Desglose por producto del catálogo: {len(filas)} filas en la primera página")
        for x in filas[:5]:
            print(f"   {float(x.get('spend', 0)):>10,.0f}  producto «{str(x.get('product_id', ''))[:60]}»")


if __name__ == "__main__":
    main()
