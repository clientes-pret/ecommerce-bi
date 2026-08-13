# Setup de la app de Reposición — pasos manuales (una sola vez)

Todo el código ya está en el repo. Lo que sigue son los pasos que **solo vos**
podés hacer (tocan tu cuenta de Supabase / GitHub), en orden.

## 1. Crear las tablas en Supabase

1. Andá a [supabase.com/dashboard](https://supabase.com/dashboard) → tu
   proyecto (el que usa `config.json["supabase"]["url"]`,
   `fvkbclkofsggbvwcxipa.supabase.co`) → **SQL Editor**.
2. Pegá y corré el contenido completo de
   [`reposicion/sql/001_setup_reposicion.sql`](sql/001_setup_reposicion.sql).
3. Verificá en **Table Editor** que aparecieron `repo_productos`,
   `repo_stock_snapshot`, `repo_calculo_semanal`, `repo_quiebre_historial`,
   `repo_pedidos`, `repo_pedido_items`, `repo_settings`.

## 2. Deployar la Edge Function (`reposicion-api`)

El Supabase CLI ya está instalado en esta máquina, pero logueado en una
cuenta que **no** tiene acceso al proyecto `fvkbclkofsggbvwcxipa` (verificado:
`supabase projects list` no lo muestra). Necesitás loguearte con la cuenta
correcta:

```bash
cd ~/Desktop/ecommerce-bi
supabase login
supabase link --project-ref fvkbclkofsggbvwcxipa
```

Elegí una contraseña compartida para el equipo (Martín/Mati/vos) y guardala
como secret de la función — **sin esto, cualquiera con la URL podría leer y
escribir los datos**:

```bash
supabase secrets set APP_SHARED_PASSWORD="elegí-algo-random-acá"
supabase functions deploy reposicion-api
```

(`supabase/config.toml` ya tiene `verify_jwt = false` para esta función —
necesario porque no usamos login de Supabase, solo la contraseña compartida.)

Al terminar el deploy, la CLI imprime la URL de la función — algo como
`https://fvkbclkofsggbvwcxipa.supabase.co/functions/v1/reposicion-api`.

## 3. Apuntar el tablero a la función

Editá [`reposicion/dashboard/config.js`](dashboard/config.js) y reemplazá
`TU-PROYECTO` por la URL real que te dio el paso anterior.

## 4. Crear el repo en GitHub y cargar los Secrets

Hoy `ecommerce-bi` es un git local sin remoto. Creá un repo **privado** en
GitHub (podés usar `gh repo create` si tenés el CLI de GitHub instalado, o
hacerlo desde la web) y pusheá:

```bash
git remote add origin git@github.com:TU-USUARIO/ecommerce-bi.git
git push -u origin master
```

Después, en **Settings → Secrets and variables → Actions** del repo, creá un
secret llamado `CONFIG_JSON` con el **contenido completo** de tu
`config.json` local (los 4 canales + la sección `supabase`). Los workflows
(`daily_stock.yml`, `weekly_calc.yml`) lo leen de ahí — nunca se commitea el
`config.json` real al repo (ya está en `.gitignore`).

## 5. Habilitar GitHub Pages

En **Settings → Pages**, en "Build and deployment" → **Source**, elegí
**"GitHub Actions"** (no "Deploy from a branch"). El workflow
`deploy-pages.yml` ya está listo — corre solo cuando cambia algo en
`reposicion/dashboard/`, o manualmente desde la pestaña Actions
("Run workflow").

La URL final del tablero va a quedar algo como
`https://TU-USUARIO.github.io/ecommerce-bi/` — esa es la que compartís con
Martín y Mati (junto con la contraseña compartida del paso 2).

## 6. Probar todo antes de confiar en el cron

No esperes al horario programado — corré cada workflow a mano primero:

1. Pestaña **Actions** del repo → "Reposición — cálculo semanal" → **Run
   workflow**. Revisá los logs; al final debería decir algo como
   `✓ N filas escritas en repo_calculo_semanal`.
2. Mismo paso con "Reposición — stock diario".
3. Abrí la URL de GitHub Pages, logueate con tu nombre + la contraseña
   compartida, y confirmá que la tabla trae productos reales (compará un par
   de SKUs contra el último `reporte_reposicion_*.xlsx` que ya tenías, para
   verificar que los números coinciden antes de confiar 100% en la app nueva).

## Notas

- `generar_reporte.py` **sigue funcionando igual que siempre** — no se tocó
  su comportamiento como script. Es un buen respaldo mientras validás la app
  nueva con datos reales.
- El valor de cobertura (`coverage_days`) vive en la tabla `repo_settings`
  (default 60). Para cambiarlo: `update repo_settings set valor = '45' where
  clave = 'coverage_days';` en el SQL Editor — el próximo cálculo semanal ya
  lo toma.
- Si algún día querés reemplazar la contraseña compartida:
  `supabase secrets set APP_SHARED_PASSWORD="nueva"` y volvé a compartir la
  nueva contraseña con el equipo (las sesiones viejas van a pedir loguearse
  de nuevo la próxima vez que la API responda 401).
