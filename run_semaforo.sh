#!/bin/bash
# Semáforo Mercado Libre — ejecución automática
# Corre los lunes a las 7:00 AM via launchd

DIR="/Users/matiaslerer/Desktop/ecommerce-bi"
LOG="$DIR/semaforo/run.log"

mkdir -p "$DIR/semaforo"
echo "========================================" >> "$LOG"
echo "Inicio: $(date)" >> "$LOG"

echo "--- Pret a Home ---" >> "$LOG"
python3 "$DIR/semaforo_ml.py" >> "$LOG" 2>&1
if [ $? -eq 0 ]; then
    echo "✅ Pret a Home OK" >> "$LOG"
else
    echo "❌ Pret a Home FALLÓ" >> "$LOG"
fi

echo "--- Casa Lavan ---" >> "$LOG"
python3 "$DIR/semaforo_ml_lavan.py" >> "$LOG" 2>&1
if [ $? -eq 0 ]; then
    echo "✅ Casa Lavan OK" >> "$LOG"
else
    echo "❌ Casa Lavan FALLÓ" >> "$LOG"
fi

echo "Fin: $(date)" >> "$LOG"
