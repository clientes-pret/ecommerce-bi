#!/usr/bin/env python3
"""
logistica/setup_db.py — Crea las tablas del sistema logístico en Neon.
Ejecutar una sola vez.
"""

import json
import psycopg2
from pathlib import Path

ROOT = Path(__file__).parent.parent
with open(ROOT / "config.json") as f:
    DB_URL = json.load(f)["database"]["url"]

SQL_FILE = Path(__file__).parent / "sql" / "001_setup_logistica.sql"

def main():
    print("Conectando a Neon...")
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()

    print("Ejecutando SQL...")
    cur.execute(SQL_FILE.read_text())

    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name LIKE 'log_%'
        ORDER BY table_name
    """)
    tablas = [r[0] for r in cur.fetchall()]
    print(f"\n✅ Tablas logísticas: {', '.join(tablas)}")

    cur.close()
    conn.close()
    print("\nBase de datos lista.")

if __name__ == "__main__":
    main()
