#!/bin/bash
# update.sh — Actualiza todos los datos y regenera el dashboard
# Uso: ./update.sh

cd "$(dirname "$0")"

echo ""
echo "════════════════════════════════════════════════"
echo "  UPDATE DASHBOARD — $(date '+%d/%m/%Y %H:%M')"
echo "════════════════════════════════════════════════"
echo ""

# 1. Tienda Nube (Pret + Lavan)
echo "▶ Sincronizando Tienda Nube..."
python3 sync.py --canal tn_pret
python3 sync.py --canal tn_lavan
echo ""

# 2. GA4
echo "▶ Sincronizando Google Analytics..."
python3 sync_ga4.py
echo ""

# 3. Mercado Libre (solo si hay token válido)
echo "▶ Sincronizando Mercado Libre..."
python3 sync.py --canal ml_pret
python3 sync.py --canal ml_lavan
echo ""

# 4. Generar dashboard
echo "▶ Generando dashboard..."
python3 report.py
echo ""

echo "════════════════════════════════════════════════"
echo "  Listo. Abrí: http://localhost:8080/dashboard.html"
echo "  (si el servidor no está corriendo: python3 -m http.server 8080)"
echo "════════════════════════════════════════════════"
echo ""
