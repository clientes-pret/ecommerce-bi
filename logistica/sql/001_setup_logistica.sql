-- ============================================================
-- SISTEMA LOGÍSTICO — Setup de base de datos
-- Ejecutar una vez contra Neon PostgreSQL
-- ============================================================

-- ── CANALES Y MARCAS ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS log_canales (
    id      TEXT PRIMARY KEY,   -- 'ml_pret' | 'ml_lavan' | 'tn_pret' | 'tn_lavan'
    nombre  TEXT NOT NULL,
    tipo    TEXT NOT NULL,       -- 'mercadolibre' | 'tiendanube'
    marca   TEXT NOT NULL        -- 'pret' | 'lavan'
);

INSERT INTO log_canales VALUES
    ('ml_pret',  'ML Pret a Home',  'mercadolibre', 'pret'),
    ('ml_lavan', 'ML Casa Lavan',   'mercadolibre', 'lavan'),
    ('tn_pret',  'TN Pret a Home',  'tiendanube',   'pret'),
    ('tn_lavan', 'TN Casa Lavan',   'tiendanube',   'lavan')
ON CONFLICT (id) DO NOTHING;

-- ── CORREOS / TIPOS DE ENVÍO ─────────────────────────────────

CREATE TABLE IF NOT EXISTS log_correos (
    id TEXT PRIMARY KEY   -- 'flex' | 'colecta' | 'cabify' | 'correo_arg' | 'andreani' | 'mauro' | 'retira_local'
);

INSERT INTO log_correos VALUES
    ('flex'), ('colecta'), ('cabify'),
    ('correo_arg'), ('andreani'), ('mauro'), ('retira_local')
ON CONFLICT (id) DO NOTHING;

-- Horarios de retiro configurados día a día por Luis
CREATE TABLE IF NOT EXISTS log_horarios_retiro (
    id          BIGSERIAL PRIMARY KEY,
    fecha       DATE NOT NULL,
    correo_id   TEXT NOT NULL REFERENCES log_correos(id),
    hora_retiro TIME NOT NULL,
    notas       TEXT,
    UNIQUE(fecha, correo_id)
);

-- ── USUARIOS OPERATIVOS ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS log_usuarios (
    id          BIGSERIAL PRIMARY KEY,
    nombre      TEXT NOT NULL,
    rol         TEXT NOT NULL,  -- 'encargado' | 'picker' | 'packer'
    activo      BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- ── PEDIDOS ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS log_pedidos (
    id                  BIGSERIAL PRIMARY KEY,

    -- Identificación
    canal_id            TEXT NOT NULL REFERENCES log_canales(id),
    external_id         TEXT NOT NULL,          -- ID en ML o TN
    numero_pedido       TEXT,                   -- número visible al cliente

    -- Tipo de envío
    correo_id           TEXT REFERENCES log_correos(id),
    shipping_type_raw   TEXT,                   -- valor original de la API

    -- Cliente
    cliente_nombre      TEXT,
    cliente_email       TEXT,

    -- Dirección de envío
    direccion           JSONB,

    -- Tiempos
    fecha_compra        TIMESTAMPTZ NOT NULL,
    sla_deadline        TIMESTAMPTZ,            -- corte de despacho (Flex/Colecta)

    -- Estado operativo
    estado              TEXT NOT NULL DEFAULT 'pendiente',
    -- pendiente → en_picking → en_espera → listo_empaquetar → listo_despacho → despachado → cancelado

    -- Asignación de dupla
    picker_id           BIGINT REFERENCES log_usuarios(id),
    packer_id           BIGINT REFERENCES log_usuarios(id),
    asignado_at         TIMESTAMPTZ,

    -- Despacho
    etiqueta_url        TEXT,
    etiqueta_impresa    BOOLEAN DEFAULT false,
    etiqueta_impresa_at TIMESTAMPTZ,
    despachado_at       TIMESTAMPTZ,

    -- Sync
    raw_payload         JSONB,                  -- respuesta original de la API
    last_synced_at      TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),

    UNIQUE(canal_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_pedidos_estado     ON log_pedidos(estado);
CREATE INDEX IF NOT EXISTS idx_pedidos_canal      ON log_pedidos(canal_id);
CREATE INDEX IF NOT EXISTS idx_pedidos_correo     ON log_pedidos(correo_id);
CREATE INDEX IF NOT EXISTS idx_pedidos_picker     ON log_pedidos(picker_id);
CREATE INDEX IF NOT EXISTS idx_pedidos_packer     ON log_pedidos(packer_id);
CREATE INDEX IF NOT EXISTS idx_pedidos_compra     ON log_pedidos(fecha_compra);

-- ── ITEMS DE PEDIDO ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS log_pedido_items (
    id              BIGSERIAL PRIMARY KEY,
    pedido_id       BIGINT NOT NULL REFERENCES log_pedidos(id) ON DELETE CASCADE,
    sku             TEXT,
    nombre_producto TEXT,
    cantidad        INTEGER NOT NULL DEFAULT 1,
    imagen_url      TEXT,
    -- Estado del item en picking
    estado_picking  TEXT DEFAULT 'pendiente',   -- 'pendiente' | 'recolectado' | 'no_encontrado'
    notas           TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_pedido ON log_pedido_items(pedido_id);

-- ── HISTORIAL DE ESTADOS ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS log_pedido_historial (
    id              BIGSERIAL PRIMARY KEY,
    pedido_id       BIGINT NOT NULL REFERENCES log_pedidos(id) ON DELETE CASCADE,
    estado_anterior TEXT,
    estado_nuevo    TEXT NOT NULL,
    usuario_id      BIGINT REFERENCES log_usuarios(id),
    notas           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_historial_pedido ON log_pedido_historial(pedido_id);

-- ── SKU STAGED (unidades extra traídas al área de packing) ──

CREATE TABLE IF NOT EXISTS log_sku_staged (
    id            BIGSERIAL PRIMARY KEY,
    fecha         DATE NOT NULL DEFAULT CURRENT_DATE,
    sku           TEXT NOT NULL,
    nombre        TEXT,
    qty_traidas   INTEGER NOT NULL DEFAULT 0,
    qty_usadas    INTEGER NOT NULL DEFAULT 0,
    picker_id     BIGINT REFERENCES log_usuarios(id),
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(fecha, sku)
);

-- ── TRIGGER: updated_at automático ──────────────────────────

CREATE OR REPLACE FUNCTION log_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pedidos_updated ON log_pedidos;
CREATE TRIGGER trg_pedidos_updated
    BEFORE UPDATE ON log_pedidos
    FOR EACH ROW EXECUTE FUNCTION log_update_timestamp();

-- ── VISTA: cola del encargado ────────────────────────────────

CREATE OR REPLACE VIEW log_cola_encargado AS
SELECT
    p.id,
    p.canal_id,
    c.nombre                                        AS canal_nombre,
    c.marca,
    p.numero_pedido,
    p.correo_id,
    p.cliente_nombre,
    p.fecha_compra,
    p.sla_deadline,
    p.estado,
    p.picker_id,
    u_picker.nombre                                 AS picker_nombre,
    p.packer_id,
    u_packer.nombre                                 AS packer_nombre,
    p.etiqueta_impresa,
    p.asignado_at,
    -- Cuánto tiempo lleva esperando sin asignar
    CASE
        WHEN p.estado = 'pendiente'
        THEN EXTRACT(EPOCH FROM (now() - p.fecha_compra)) / 60
        ELSE NULL
    END                                             AS minutos_esperando,
    -- Cantidad de items
    COUNT(i.id)                                     AS total_items,
    SUM(i.cantidad)                                 AS total_unidades,
    SUM(CASE WHEN i.estado_picking = 'recolectado' THEN 1 ELSE 0 END) AS items_recolectados,
    SUM(CASE WHEN i.estado_picking = 'no_encontrado' THEN 1 ELSE 0 END) AS items_faltantes
FROM log_pedidos p
JOIN log_canales c ON c.id = p.canal_id
LEFT JOIN log_usuarios u_picker ON u_picker.id = p.picker_id
LEFT JOIN log_usuarios u_packer ON u_packer.id = p.packer_id
LEFT JOIN log_pedido_items i ON i.pedido_id = p.id
WHERE p.estado NOT IN ('despachado', 'cancelado')
GROUP BY p.id, c.nombre, c.marca, u_picker.nombre, u_packer.nombre
ORDER BY
    CASE p.correo_id
        WHEN 'flex'    THEN 1
        WHEN 'colecta' THEN 2
        WHEN 'cabify'  THEN 3
        WHEN 'andreani'THEN 4
        WHEN 'mauro'   THEN 4
        WHEN 'correo_arg' THEN 4
        WHEN 'retira_local' THEN 5
        ELSE 6
    END,
    p.sla_deadline ASC NULLS LAST,
    p.fecha_compra ASC;
