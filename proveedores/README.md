# Proveedores — recepción de mercadería + cuenta corriente

Mati (depósito) carga lo que ingresa desde el celu; Jackie (administración) controla contra la factura y lleva la cuenta corriente de cada proveedor.
Mismo stack que Reposición: Supabase (proyecto `fvkbclkofsggbvwcxipa`) + HTML/JS sin build en GitHub Pages + Edge Function.

| Qué | Dónde |
|---|---|
| Pantallas (GitHub Pages) | `reposicion/dashboard/proveedores/` → https://clientes-pret.github.io/ecommerce-bi/proveedores/ |
| Tablas `cc_*`, vistas y seguridad (RLS) | `supabase/migrations/20261014180000_cc_cuenta_corriente.sql` y `…180100_cc_revocar_anon.sql` |
| Lectura del remito con IA | `supabase/functions/leer-remito/index.ts` |
| Importación y alta de usuarios | `proveedores/scripts/` |
| Datos de la planilla de Jackie (NO se sube a GitHub: `data/` está en .gitignore) | `proveedores/data/` |

Pantallas: `recibir.html` (Mati, celu) · `admin.html` (inicio de Jackie) · `proveedores.html` y `proveedor.html?id=` (cuenta corriente) · `recepcion.html?id=` (controlar) · `nuevo.html` (registrar factura/pago/NC/ajuste).

## Seguridad
- Cada persona entra con su usuario (Supabase Auth). Roles: `deposito` (Mati), `admin` (Jackie), `dueno`.
- El rol `deposito` **no puede leer** `cc_movimientos`, `cc_cuenta_corriente` ni `cc_saldos` (lo hace cumplir Postgres con RLS, no la pantalla).
- Las fotos de remitos están en un bucket privado `remitos`.
- Desactivar el registro público de usuarios: Supabase → Authentication → Sign In / Providers → apagar "Allow new users to sign up".
  (Aunque alguien se registre, sin fila en `cc_perfiles` no ve nada.)

## Puesta en marcha (una vez) — todo desde la carpeta `ecommerce-bi`
1. `supabase db push` — crea las tablas.
2. `venv/bin/python proveedores/scripts/importar.py` — carga los 3.623 movimientos y 7.616 SKUs; al final compara los totales con la planilla.
3. Usuarios:
   ```
   venv/bin/python proveedores/scripts/crear_usuario.py jackie@... "Jackie" admin "clave"
   venv/bin/python proveedores/scripts/crear_usuario.py mati@... "Mati" deposito "clave"
   venv/bin/python proveedores/scripts/crear_usuario.py matiaslerer@gmail.com "Matías" dueno "clave"
   ```
4. Pegar la *anon public key* en `reposicion/dashboard/proveedores/config.js` (Supabase → Project Settings → API).
5. IA para remitos:
   ```
   supabase secrets set ANTHROPIC_API_KEY=sk-ant-...
   supabase functions deploy leer-remito
   ```
6. Publicar: `git add reposicion/dashboard/proveedores supabase proveedores/README.md proveedores/scripts .gitignore` → commit → push a `master` (GitHub Pages se actualiza solo).
7. En el celu de Mati: abrir la URL → "Agregar a pantalla de inicio".

## Notas
- Los nombres de proveedor se importaron tal cual (cada variante M/A/Z/P es una cuenta, así los saldos coinciden con la hoja "Proveedor"); `grupo` permite unificarlas.
- Un ajuste de la planilla sin proveedor (20/07/2026, $69.128) queda en el proveedor "SIN ASIGNAR".
- Proveedores sin movimientos en 12 meses se importan como inactivos (no aparecen para Mati; Jackie los puede reactivar).
- Los códigos del proveedor en sus remitos se asocian a tu SKU la primera vez que Mati los elige (`cc_alias_sku`).
