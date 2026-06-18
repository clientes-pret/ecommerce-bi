#!/usr/bin/env python3
"""
logistica/server.py — Servidor local del dashboard logístico.
Correr en la PC del depósito: python server.py
Luego abrir: http://localhost:8080
"""

import json
import psycopg2
import psycopg2.extras
from pathlib import Path
from datetime import date, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import os

ROOT = Path(__file__).parent.parent

# En Railway usa variable de entorno, localmente usa config.json
DB_URL = os.environ.get("DATABASE_URL")
if not DB_URL:
    with open(ROOT / "config.json") as f:
        DB_URL = json.load(f)["database"]["url"]

def get_conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)

# ── API handlers ──────────────────────────────────────────────────────────────

def api_cola(params):
    """Devuelve la cola de pedidos para el dashboard de Luis."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            p.id,
            p.canal_id,
            c.nombre AS canal_nombre,
            c.marca,
            p.numero_pedido,
            p.correo_id,
            p.cliente_nombre,
            p.fecha_compra,
            p.sla_deadline,
            p.estado,
            p.picker_id,
            u_picker.nombre AS picker_nombre,
            p.packer_id,
            u_packer.nombre AS packer_nombre,
            p.etiqueta_impresa,
            p.asignado_at,
            EXTRACT(EPOCH FROM (now() - p.fecha_compra)) / 60 AS minutos_esperando,
            (SELECT COUNT(*) FROM log_pedido_items WHERE pedido_id = p.id) AS total_items,
            (SELECT COALESCE(SUM(cantidad),0) FROM log_pedido_items WHERE pedido_id = p.id) AS total_unidades,
            (SELECT COUNT(*) FROM log_pedido_items WHERE pedido_id = p.id AND estado_picking = 'recolectado') AS items_recolectados,
            (SELECT COUNT(*) FROM log_pedido_items WHERE pedido_id = p.id AND estado_picking = 'no_encontrado') AS items_faltantes
        FROM log_pedidos p
        JOIN log_canales c ON c.id = p.canal_id
        LEFT JOIN log_usuarios u_picker ON u_picker.id = p.picker_id
        LEFT JOIN log_usuarios u_packer ON u_packer.id = p.packer_id
        WHERE p.estado NOT IN ('despachado', 'cancelado')
          AND p.fecha_compra >= CURRENT_DATE
        ORDER BY
            CASE p.correo_id
                WHEN 'flex'         THEN 1
                WHEN 'colecta'      THEN 2
                WHEN 'cabify'       THEN 3
                WHEN 'andreani'     THEN 4
                WHEN 'mauro'        THEN 4
                WHEN 'correo_arg'   THEN 4
                WHEN 'retira_local' THEN 5
                ELSE 6
            END,
            p.sla_deadline ASC NULLS LAST,
            p.fecha_compra ASC
    """)
    rows = cur.fetchall()
    conn.close()

    # Serializar fechas
    result = []
    for r in rows:
        d = dict(r)
        for k, v in d.items():
            if isinstance(v, (datetime, date)):
                d[k] = v.isoformat()
            elif hasattr(v, '__float__'):
                d[k] = float(v) if v is not None else None
        result.append(d)
    return result

def api_resumen_correos(params):
    """Conteo de pedidos por correo y estado."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            correo_id,
            estado,
            COUNT(*) as total
        FROM log_pedidos
        WHERE estado NOT IN ('despachado', 'cancelado')
        GROUP BY correo_id, estado
        ORDER BY correo_id, estado
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def api_horarios(params):
    """Horarios de retiro de hoy."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT correo_id, hora_retiro::text, notas
        FROM log_horarios_retiro
        WHERE fecha = CURRENT_DATE
        ORDER BY hora_retiro
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def api_horarios_save(body):
    """Guarda el horario de un correo para hoy."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO log_horarios_retiro (fecha, correo_id, hora_retiro, notas)
        VALUES (CURRENT_DATE, %(correo_id)s, %(hora_retiro)s, %(notas)s)
        ON CONFLICT (fecha, correo_id) DO UPDATE SET
            hora_retiro = EXCLUDED.hora_retiro,
            notas = EXCLUDED.notas
    """, body)
    conn.commit()
    conn.close()
    return {"ok": True}

def api_usuarios(params):
    """Lista de usuarios activos."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, nombre, rol FROM log_usuarios WHERE activo = true ORDER BY rol, nombre")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def api_usuarios_save(body):
    """Crea un usuario nuevo."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO log_usuarios (nombre, rol)
        VALUES (%(nombre)s, %(rol)s)
        RETURNING id, nombre, rol
    """, body)
    row = dict(cur.fetchone())
    conn.commit()
    conn.close()
    return row

def api_asignar(body):
    """Asigna picker y packer a un lote de pedidos."""
    conn = get_conn()
    cur = conn.cursor()
    ids = body.get("pedido_ids", [])
    picker_id = body.get("picker_id")
    packer_id = body.get("packer_id")

    for pid in ids:
        cur.execute("""
            UPDATE log_pedidos SET
                picker_id   = %s,
                packer_id   = %s,
                estado      = 'en_picking',
                asignado_at = now()
            WHERE id = %s AND estado = 'pendiente'
        """, (picker_id, packer_id, pid))
        cur.execute("""
            INSERT INTO log_pedido_historial (pedido_id, estado_anterior, estado_nuevo, notas)
            VALUES (%s, 'pendiente', 'en_picking', 'Asignado por encargado')
        """, (pid,))

    conn.commit()
    conn.close()
    return {"ok": True, "asignados": len(ids)}

def api_items_pedido(params):
    """Items de un pedido específico."""
    pedido_id = params.get("id", [None])[0]
    if not pedido_id:
        return []
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, sku, nombre_producto, cantidad, imagen_url, estado_picking, notas
        FROM log_pedido_items
        WHERE pedido_id = %s
        ORDER BY id
    """, (pedido_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── HTTP Handler ──────────────────────────────────────────────────────────────

def api_picker_ordenes(params):
    """Órdenes asignadas a un picker con items e info de extras."""
    picker_id = params.get("picker_id", [None])[0]
    if not picker_id:
        return []
    conn = get_conn()
    cur = conn.cursor()

    # Órdenes asignadas al picker que no están despachadas
    cur.execute("""
        SELECT
            p.id, p.canal_id, c.nombre AS canal_nombre,
            p.numero_pedido, p.correo_id, p.estado,
            p.fecha_compra, p.sla_deadline
        FROM log_pedidos p
        JOIN log_canales c ON c.id = p.canal_id
        WHERE p.picker_id = %s
          AND p.estado IN ('en_picking', 'listo_empaquetar', 'en_espera')
          AND DATE(p.asignado_at) = CURRENT_DATE
        ORDER BY
            CASE p.correo_id WHEN 'flex' THEN 1 WHEN 'colecta' THEN 2 ELSE 3 END,
            p.sla_deadline ASC NULLS LAST
    """, (picker_id,))
    ordenes = [dict(r) for r in cur.fetchall()]

    # Para cada orden, traer items con cantidad pendiente en otras órdenes del día
    for orden in ordenes:
        cur.execute("""
            SELECT
                i.id, i.sku, i.nombre_producto, i.cantidad,
                i.imagen_url, i.estado_picking,
                -- Cuántas unidades del mismo SKU están pendientes HOY en otras órdenes
                COALESCE((
                    SELECT SUM(i2.cantidad)
                    FROM log_pedido_items i2
                    JOIN log_pedidos p2 ON p2.id = i2.pedido_id
                    WHERE i2.sku = i.sku
                      AND p2.id != i.pedido_id
                      AND p2.estado IN ('pendiente', 'en_picking')
                      AND DATE(p2.created_at) >= CURRENT_DATE - INTERVAL '1 day'
                ), 0) AS qty_pendiente_hoy
            FROM log_pedido_items i
            WHERE i.pedido_id = %s
        """, (orden['id'],))
        items = [dict(r) for r in cur.fetchall()]
        # Serializar decimales
        for it in items:
            for k, v in it.items():
                if hasattr(v, '__float__'):
                    it[k] = int(v) if v is not None else 0
        orden['items'] = items

        # Serializar fechas
        for k, v in orden.items():
            if isinstance(v, (datetime, date)):
                orden[k] = v.isoformat()

    conn.close()
    return ordenes

def api_sku_staged(params):
    """Stock temporario en zona packing para hoy."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT sku, nombre, qty_traidas, qty_usadas,
               (qty_traidas - qty_usadas) AS qty_disponible
        FROM log_sku_staged
        WHERE fecha = CURRENT_DATE AND qty_traidas > qty_usadas
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        for k, v in r.items():
            if hasattr(v, '__float__') or hasattr(v, '__int__'):
                r[k] = int(v) if v is not None else 0
    return rows

def api_sku_staged_save(body):
    """Registra unidades extra traídas al área de packing."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO log_sku_staged (fecha, sku, nombre, qty_traidas, picker_id)
        VALUES (CURRENT_DATE, %(sku)s, %(nombre)s, %(qty)s, %(picker_id)s)
        ON CONFLICT (fecha, sku) DO UPDATE SET
            qty_traidas = log_sku_staged.qty_traidas + EXCLUDED.qty_traidas
    """, body)
    conn.commit()
    conn.close()
    return {"ok": True}

def api_picker_listo(body):
    """Marca un pedido como listo para empaquetar."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE log_pedidos SET estado = 'listo_empaquetar'
        WHERE id = %s AND estado = 'en_picking'
    """, (body['pedido_id'],))
    cur.execute("""
        INSERT INTO log_pedido_historial (pedido_id, estado_anterior, estado_nuevo, usuario_id)
        VALUES (%s, 'en_picking', 'listo_empaquetar', %s)
    """, (body['pedido_id'], body['picker_id']))
    conn.commit()
    conn.close()
    return {"ok": True}

def api_picker_problema(body):
    """Marca un pedido en espera con tipo de problema."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE log_pedidos SET estado = 'en_espera'
        WHERE id = %s AND estado = 'en_picking'
    """, (body['pedido_id'],))
    cur.execute("""
        INSERT INTO log_pedido_historial (pedido_id, estado_anterior, estado_nuevo, usuario_id, notas)
        VALUES (%s, 'en_picking', 'en_espera', %s, %s)
    """, (body['pedido_id'], body['picker_id'], body.get('tipo', 'sin_detalle')))
    conn.commit()
    conn.close()
    return {"ok": True}

ROUTES_GET = {
    "/api/cola":            api_cola,
    "/api/resumen-correos": api_resumen_correos,
    "/api/horarios":        api_horarios,
    "/api/usuarios":        api_usuarios,
    "/api/items":           api_items_pedido,
    "/api/picker-ordenes":  api_picker_ordenes,
    "/api/sku-staged":      api_sku_staged,
}

ROUTES_POST = {
    "/api/horarios":        api_horarios_save,
    "/api/asignar":         api_asignar,
    "/api/usuarios":        api_usuarios_save,
    "/api/sku-staged":      api_sku_staged_save,
    "/api/picker-listo":    api_picker_listo,
    "/api/picker-problema": api_picker_problema,
}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silenciar logs de requests

    def send_json(self, data, status=200):
        try:
            body = json.dumps(data, ensure_ascii=False, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def send_file(self, path):
        content = Path(path).read_bytes()
        ext = Path(path).suffix
        types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
        self.send_response(200)
        self.send_header("Content-Type", types.get(ext, "text/plain") + "; charset=utf-8")
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path in ROUTES_GET:
            try:
                result = ROUTES_GET[path](params)
                self.send_json(result)
            except BrokenPipeError:
                pass
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        elif path == "/" or path == "/index.html":
            self.send_file(Path(__file__).parent / "dashboard" / "index.html")
        elif path == "/picker.html":
            self.send_file(Path(__file__).parent / "dashboard" / "picker.html")
        elif path == "/packer.html":
            self.send_file(Path(__file__).parent / "dashboard" / "packer.html")
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if path in ROUTES_POST:
            try:
                result = ROUTES_POST[path](body)
                self.send_json(result)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        else:
            self.send_json({"error": "not found"}, 404)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))
    HOST = "0.0.0.0"
    server = HTTPServer((HOST, PORT), Handler)
    print(f"✅ Servidor logístico corriendo en http://localhost:{PORT}")
    print(f"   Dashboard Luis:  http://localhost:{PORT}/")
    print(f"   App Picker:      http://localhost:{PORT}/picker.html")
    print(f"   App Packer:      http://localhost:{PORT}/packer.html")
    print("\nCtrl+C para detener\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
