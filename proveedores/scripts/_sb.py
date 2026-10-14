"""Helpers mínimos contra Supabase (REST + Auth admin). Lee url y service_role_key de ecommerce-bi/config.json,
el mismo que usan los jobs de reposición. Solo stdlib."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_cfg = json.load(open(os.path.join(RAIZ, "config.json"), encoding="utf-8"))["supabase"]
URL = _cfg["url"].rstrip("/")
KEY = _cfg["service_role_key"]


def _req(method, path, body=None, headers=None):
    h = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(URL + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"❌ {method} {path} → {e.code}: {e.read().decode()[:400]}")


def select(tabla, query=""):
    return _req("GET", f"/rest/v1/{tabla}?{query}")


def insert(tabla, filas, extra=None):
    h = {"Prefer": "return=minimal"}
    h.update(extra or {})
    return _req("POST", f"/rest/v1/{tabla}", filas, h)


def upsert(tabla, filas, on_conflict, ignore=False):
    pref = "resolution=ignore-duplicates" if ignore else "resolution=merge-duplicates"
    return _req("POST", f"/rest/v1/{tabla}?on_conflict={on_conflict}", filas, {"Prefer": f"{pref},return=minimal"})


def delete(tabla, filtro):
    return _req("DELETE", f"/rest/v1/{tabla}?{filtro}", None, {"Prefer": "return=minimal"})


def crear_usuario_auth(email, password):
    return _req("POST", "/auth/v1/admin/users", {"email": email, "password": password, "email_confirm": True})
