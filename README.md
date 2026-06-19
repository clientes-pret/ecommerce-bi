# BI Dashboard — Pret a Home / Casa Lavan

## Setup inicial

```bash
cd ecommerce-bi
pip3 install requests
```

## Primera corrida (baja todo desde 2025-01-01)

```bash
# 1. Actualizar el token de ML en config.json
#    (token ML_PRET expira cada 6h, regenerarlo en developers.mercadolibre.com.ar)

# 2. Sync completo
python3 sync.py

# 3. Generar dashboard
python3 report.py

# 4. Abrir
open dashboard.html
```

## Uso diario (sync incremental)

```bash
python3 sync.py && python3 report.py && open dashboard.html
```

Solo baja lo nuevo desde el último sync. Tarda ~30 segundos.

## Renovar token ML

El token de ML dura 6 horas. Cuando expire:

```bash
curl -X POST "https://api.mercadolibre.com/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=1637574709714032&client_secret=TU_SECRET"
```

Pegar el `access_token` en `config.json` → campo `ml_pret.access_token`.

## Filtrar por período

```bash
python3 report.py --desde 2025-03-01 --hasta 2025-06-30
```

## Agregar Casa Lavan ML (cuando tengas las credenciales)

1. Editar `config.json`:
   - Cambiar `ml_lavan.enabled` a `true`
   - Completar `user_id` y `access_token`

2. Correr solo ese canal:
   ```bash
   python3 sync.py --canal ml_lavan
   ```

3. Regenerar el dashboard:
   ```bash
   python3 report.py && open dashboard.html
   ```

**No toca los otros canales.** Solo descarga el historial de ml_lavan.

## Estructura de archivos

```
ecommerce-bi/
  config.json          ← credenciales y configuración
  sync.py              ← motor de sync incremental
  report.py            ← generador de dashboard
  README.md
  data/
    cache_ml_pret.json     ← órdenes + catálogo ML Pret
    cache_tn_pret.json     ← órdenes + stock + costos TN Pret
    cache_tn_lavan.json    ← órdenes TN Lavan
    last_sync.json         ← timestamps de último sync por canal
  dashboard.html        ← output (no subir a git)
```

## Stock

El stock viene ÚNICAMENTE de `tn_pret` (fuente de verdad).
No se suma stock de otros canales — es el mismo stock físico.

## Health Score

| Componente  | Peso | Qué mide |
|-------------|------|----------|
| Ventas      | 30%  | Unidades vs percentil 90 del catálogo |
| Margen      | 25%  | (precio - costo) / precio |
| Rotación    | 25%  | Días de stock vs benchmark (30d ideal) |
| Velocidad   | 20%  | Tendencia últimas 4 semanas vs anteriores |

Rango: 0-100. Verde ≥75, Amarillo ≥50, Naranja ≥25, Rojo <25.
