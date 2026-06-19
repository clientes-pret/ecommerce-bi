#!/usr/bin/env python3
"""
auth_ml.py — Autorización OAuth de Mercado Libre

Corré este script UNA SOLA VEZ por cuenta ML para obtener el refresh token.
Después sync.py lo usa automáticamente para renovar el access token.

Uso:
    python3 auth_ml.py --canal ml_pret
    python3 auth_ml.py --canal ml_lavan

Requisito:
    Tu app de ML debe tener https://localhost como Redirect URI
    (configuralo en developers.mercadolibre.com.ar)
"""

import json, requests, webbrowser, argparse
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

ROOT       = Path(__file__).parent
DATA_DIR   = ROOT / "data"
TOKENS_FILE= DATA_DIR / "tokens.json"
ML_BASE    = "https://api.mercadolibre.com"
REDIRECT   = "https://httpbin.org/get"

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def load_config():
    with open(ROOT / "config.json") as f:
        return json.load(f)

def load_tokens():
    if TOKENS_FILE.exists():
        with open(TOKENS_FILE) as f:
            return json.load(f)
    return {}

def save_tokens(tokens):
    DATA_DIR.mkdir(exist_ok=True)
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    print(f"  Tokens guardados en {TOKENS_FILE}")

# ─── CAPTURAR CÓDIGO OAUTH ───────────────────────────────────────────────────

captured_code = None

class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global captured_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "code" in params:
            captured_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>OK</h2><p>Cerrar esta ventana.</p></body></html>")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # silenciar logs del servidor

def get_auth_code(client_id):
    """Abre el browser para autorizar y captura el código via servidor local."""
    global captured_code
    captured_code = None

    auth_url = (
        f"https://auth.mercadolibre.com.ar/authorization"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={REDIRECT}"
    )

    print(f"\n  Abriendo browser para autorizar...")
    print(f"  Si no se abre automáticamente, entrá a:\n  {auth_url}\n")

    webbrowser.open(auth_url)

    print("  Después de autorizar, el browser te va a llevar a una página de httpbin.org")
    print("  Buscá en el JSON el campo 'code' dentro de 'args' y copiá su valor.\n")
    print('  Ejemplo: "args": { "code": "TU-CODIGO-AQUI" }\n')
    code = input("  Pegá el código aquí: ").strip()
    return code

# ─── INTERCAMBIAR CÓDIGO POR TOKENS ──────────────────────────────────────────

def exchange_code(client_id, client_secret, code):
    r = requests.post(f"{ML_BASE}/oauth/token", data={
        "grant_type":    "authorization_code",
        "client_id":     client_id,
        "client_secret": client_secret,
        "code":          code,
        "redirect_uri":  REDIRECT,
    })
    if r.status_code != 200:
        print(f"  Error: {r.status_code} — {r.text}")
        return None
    return r.json()

def refresh_access_token(client_id, client_secret, refresh_token):
    """Renueva el access token usando el refresh token."""
    r = requests.post(f"{ML_BASE}/oauth/token", data={
        "grant_type":    "refresh_token",
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    })
    if r.status_code != 200:
        return None
    return r.json()

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--canal", required=True, help="Canal ML a autorizar (ml_pret o ml_lavan)")
    parser.add_argument("--test",  action="store_true", help="Solo testear el token guardado")
    args = parser.parse_args()

    config = load_config()
    canal  = args.canal

    if canal not in config["channels"]:
        print(f"Canal '{canal}' no encontrado en config.json")
        return

    cfg = config["channels"][canal]
    if cfg["type"] != "mercadolibre":
        print(f"'{canal}' no es un canal de Mercado Libre")
        return

    # Obtener client_id y client_secret
    # El client_id está en el access_token (primer segmento después de APP_USR-)
    # Pero lo mejor es pedirlo explícitamente
    print(f"\n  Autorización OAuth — {cfg['label']}")
    print(f"  {'─'*48}")

    tokens = load_tokens()

    # Si ya tiene refresh token, testearlo
    if canal in tokens and tokens[canal].get("refresh_token"):
        print(f"  Ya hay un refresh token guardado para {cfg['label']}")

        if args.test:
            print("  Testeando renovación...")
            client_id     = input("  Client ID: ").strip()
            client_secret = input("  Client Secret: ").strip()
            result = refresh_access_token(client_id, client_secret, tokens[canal]["refresh_token"])
            if result:
                tokens[canal].update(result)
                save_tokens(tokens)
                print(f"  ✅ Token renovado. Expira en {result.get('expires_in',0)//3600}h")
            else:
                print("  ❌ Refresh token inválido, necesitás re-autorizar")
            return
        else:
            resp = input("  ¿Re-autorizar de todas formas? (s/N): ").strip().lower()
            if resp != "s":
                print("  OK, usando el token existente.")
                return

    # Pedir credenciales
    print(f"\n  Necesito el Client ID y Client Secret de tu app de ML")
    print(f"  (los encontrás en developers.mercadolibre.com.ar → tu app → Credenciales)\n")
    client_id     = input("  Client ID: ").strip()
    client_secret = input("  Client Secret: ").strip()

    # Obtener código de autorización
    code = get_auth_code(client_id)
    if not code:
        print("  No se recibió el código de autorización")
        return

    # Intercambiar por tokens
    print("\n  Intercambiando código por tokens...")
    result = exchange_code(client_id, client_secret, code)
    if not result:
        print("  ❌ Error al obtener tokens")
        return

    # Guardar
    tokens[canal] = {
        "access_token":  result.get("access_token"),
        "refresh_token": result.get("refresh_token"),
        "expires_in":    result.get("expires_in"),
        "user_id":       str(result.get("user_id", "")),
        "client_id":     client_id,
        "client_secret": client_secret,
    }
    save_tokens(tokens)

    # Actualizar config.json con el nuevo access token
    config["channels"][canal]["access_token"] = result.get("access_token", "")
    config["channels"][canal]["user_id"]       = str(result.get("user_id", cfg.get("user_id","")))
    with open(ROOT / "config.json", "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n  ✅ Autorización exitosa para {cfg['label']}")
    print(f"  Access token: válido por {result.get('expires_in',0)//3600} horas")
    print(f"  Refresh token: guardado (válido por 6 meses, se renueva automáticamente)")
    print(f"\n  Ya podés correr: python3 sync.py --canal {canal}")

if __name__ == "__main__":
    main()
