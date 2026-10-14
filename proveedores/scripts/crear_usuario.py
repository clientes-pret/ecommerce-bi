"""Crea un usuario de la app de Proveedores.  Uso (desde la carpeta ecommerce-bi):
    venv/bin/python proveedores/scripts/crear_usuario.py email@dominio.com "Nombre" deposito|admin|dueno "contraseña"
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import _sb as sb

if len(sys.argv) != 5 or sys.argv[3] not in ("deposito", "admin", "dueno"):
    raise SystemExit(__doc__)
email, nombre, rol, password = sys.argv[1:]
u = sb.crear_usuario_auth(email, password)
sb.upsert("cc_perfiles", [{"id": u["id"], "nombre": nombre, "rol": rol}], "id")
print(f"✅ {nombre} ({rol}) creado: {email}")
