# Handoff: panel de control (Ventas · Proveedores · Costos · Cashflow · P&L)

Contexto armado en una conversación de Cowork del 2026-10-23. Leelo antes de seguir.

## Objetivo
Un panel de control simple para Pret a Home / Casa Laván que junte: Ventas (= el Scorecard diario actual),
Proveedores y Costos (= lo que ya existe en reposicion/dashboard/proveedores, se trae tal cual),
y dos pantallas nuevas: Cashflow y P&L. Reposición queda afuera del panel. No hay ítem "Rentabilidad" en el menú.

## Diseño aprobado
Canvas con las 6 pantallas (mockups con datos de ejemplo): https://claude.ai/artifact/9YQK8szdd2G5ZSqUdJgFcq
- Estilo: fondo #F5F4F0, tarjetas blancas, Inter, acento #2449B8, alertas naranja #B54708. Colores por marca del
  scorecard: Pret #7A3B46, Laván #2B6D68.
- Menú lateral oscuro con logo blanco (pret-a-home-hub/assets/logo-white.png) arriba de "Panel de control":
  Inicio, Ventas | Gestión: Proveedores, Costos, Cashflow, P&L.
- Docs de referencia: "Cashflow y P&L: qué tomar de otras plataformas" https://claude.ai/artifact/Y2WV7FxzHM1doNvtLj4k3w

## Definiciones
- P&L (devengado, por mes): ventas − costo de productos − envíos/comisiones (= neta del scorecard) − ads − costos fijos
  − costos variables manuales (cc_costos). Corte por marca y canal, comparado con el mismo mes del año anterior.
- Cashflow (percibido, por mes): liberaciones ML/TN + local − pagos a proveedores (cc_movimientos) − costos pagados.
  Grilla categoría × mes, real vs proyectado, caja mínima con semáforo. Falta tabla de saldos de bancos/MercadoPago.

## Migración del Scorecard (artifact → Supabase) — estado
- Scorecard actual: https://claude.ai/artifact/Sr3MfkC7A9onAvQRD3uNYs (datos en la base del artifact, colección "days").
- Proyecto Supabase: fvkbclkofsggbvwcxipa (el de reposición/proveedores).
- Migración escrita: supabase/migrations/20261023190000_scorecard.sql (scorecard_canal_dia, scorecard_ads_dia,
  scorecard_notas, v_scorecard_mensual, v_scorecard_ads_mensual; RLS admin/dueño vía cc_rol()).
- Historia exportada: scorecard/data/artifact_days.json (151 días, 2026-05-13 a 2026-10-22).
- Script: scorecard/migrar_desde_artifact.py (simula por defecto; --aplicar carga con upsert).
- PENDIENTE de correr: `supabase db push` y `venv/bin/python scorecard/migrar_desde_artifact.py --aplicar`.

## Próximos pasos
1. Verificar la carga (filas por mes vs el artifact).
2. Carga diaria automática del scorecard a Supabase (script en ecommerce-bi o Edge Function) que reemplace
   el botón "Actualizar hoy" y la carga manual.
3. Pantalla Ventas del panel leyendo de scorecard_canal_dia / scorecard_ads_dia.
4. Vistas v_pyl_mensual y v_cashflow_mensual + tabla saldos_cuentas; pantallas P&L y Cashflow.
5. Unificar el admin de proveedores al estilo del canvas.
