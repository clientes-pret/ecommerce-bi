#!/usr/bin/env python3
"""
setup_db.py — Crea las tablas en Neon PostgreSQL.
Ejecutar una sola vez (o cuando se quiera resetear el esquema).
"""

import json
import psycopg2
from pathlib import Path

with open(Path(__file__).parent / "config.json") as f:
    DB_URL = json.load(f)["database"]["url"]

DDL = """
-- ── 1. SEMÁFORO ML ────────────────────────────────────────────────────────────
-- Snapshot diario de publicaciones ML con actividad (visitas>0 o ventas>0).
-- Una fila por publicación por día, separado por canal (ml_pret / ml_lavan).

CREATE TABLE IF NOT EXISTS semaforo_publicaciones (
    id          BIGSERIAL PRIMARY KEY,
    fecha       DATE        NOT NULL,
    canal       TEXT        NOT NULL,   -- ml_pret | ml_lavan
    item_id     TEXT        NOT NULL,
    sku         TEXT,
    titulo      TEXT,
    precio      NUMERIC(12,2),
    stock       INTEGER,
    visitas     INTEGER,
    ventas      INTEGER,
    conversion  NUMERIC(6,3),           -- porcentaje
    vel_semanal NUMERIC(8,2),
    categoria   TEXT,                   -- Ganador | Oportunidad | Dormido | Muerto
    logistica   TEXT,
    permalink   TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_semaforo_unico
    ON semaforo_publicaciones (fecha, canal, item_id);

CREATE INDEX IF NOT EXISTS idx_semaforo_fecha  ON semaforo_publicaciones (fecha);
CREATE INDEX IF NOT EXISTS idx_semaforo_canal  ON semaforo_publicaciones (canal);
CREATE INDEX IF NOT EXISTS idx_semaforo_sku    ON semaforo_publicaciones (sku);


-- ── 2. VENTAS POR SKU Y CANAL ─────────────────────────────────────────────────
-- Ventas diarias de cada SKU en cada uno de los 4 canales.
-- Solo se guarda si hubo al menos 1 venta ese día en ese canal.

CREATE TABLE IF NOT EXISTS ventas_sku_canal (
    id        BIGSERIAL PRIMARY KEY,
    fecha     DATE    NOT NULL,
    canal     TEXT    NOT NULL,   -- ml_pret | ml_lavan | tn_pret | tn_lavan
    sku       TEXT    NOT NULL,
    titulo    TEXT,
    unidades  INTEGER NOT NULL DEFAULT 0,
    revenue   NUMERIC(14,2) NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ventas_unico
    ON ventas_sku_canal (fecha, canal, sku);

CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON ventas_sku_canal (fecha);
CREATE INDEX IF NOT EXISTS idx_ventas_sku   ON ventas_sku_canal (sku);
CREATE INDEX IF NOT EXISTS idx_ventas_canal ON ventas_sku_canal (canal);


-- ── 3. STOCK SNAPSHOT ─────────────────────────────────────────────────────────
-- Estado de stock diario por SKU (fuente: tn_pret).
-- Incluye velocidad y días de cobertura para alertas de reposición.

CREATE TABLE IF NOT EXISTS stock_snapshot (
    id          BIGSERIAL PRIMARY KEY,
    fecha       DATE    NOT NULL,
    sku         TEXT    NOT NULL,
    titulo      TEXT,
    stock       INTEGER NOT NULL DEFAULT 0,
    vel_diaria  NUMERIC(8,4),
    vel_mensual NUMERIC(8,2),
    dias_stock  INTEGER,           -- NULL si vel=0
    urgencia    TEXT               -- urgente | critico | planificar | analizar | ok
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_unico
    ON stock_snapshot (fecha, sku);

CREATE INDEX IF NOT EXISTS idx_stock_fecha    ON stock_snapshot (fecha);
CREATE INDEX IF NOT EXISTS idx_stock_sku      ON stock_snapshot (sku);
CREATE INDEX IF NOT EXISTS idx_stock_urgencia ON stock_snapshot (urgencia);


-- ── 4. PROVEEDORES ────────────────────────────────────────────────────────────
-- Tabla estática: SKU → proveedor, lead time y cobertura objetivo.
-- Se arma una vez y se mantiene manualmente.

CREATE TABLE IF NOT EXISTS proveedores (
    sku              TEXT PRIMARY KEY,
    proveedor        TEXT NOT NULL,
    lead_time_dias   INTEGER NOT NULL DEFAULT 30,
    cobertura_dias   INTEGER NOT NULL DEFAULT 60,
    qty_minima       INTEGER NOT NULL DEFAULT 1,
    notas            TEXT
);
"""

def main():
    print("Conectando a Neon...")
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()

    print("Creando tablas...")
    cur.execute(DDL)

    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tablas = [r[0] for r in cur.fetchall()]
    print(f"\n✅ Tablas creadas: {', '.join(tablas)}")

    cur.close()
    conn.close()
    print("\nBase de datos lista.")

if __name__ == "__main__":
    main()
