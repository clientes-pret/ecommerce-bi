// supabase/functions/reposicion-api/index.ts
//
// Gateway único de la app de Reposición. El frontend (reposicion/dashboard/,
// hosteado gratis en GitHub Pages) nunca ve el service_role_key — le pega a
// esta función, que valida la contraseña compartida del equipo (header
// X-App-Password) y adentro usa el service_role_key para leer/escribir en
// Postgres. SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY los inyecta Supabase
// automáticamente en cada Edge Function; APP_SHARED_PASSWORD hay que
// setearlo a mano una vez (ver reposicion/SETUP.md).

import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-App-Password",
};

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

const APP_PASSWORD = Deno.env.get("APP_SHARED_PASSWORD") ?? "";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

function checkPassword(req: Request): boolean {
  if (!APP_PASSWORD) return true; // sin password configurado -> no bloquea (setup inicial)
  return req.headers.get("x-app-password") === APP_PASSWORD;
}

// ─── Helpers de agregación ──────────────────────────────────────────────────

// deno-lint-ignore no-explicit-any
type QueryBuilder = any;

/** Trae TODAS las filas de una tabla, paginando con .range() — el cliente de
 * Supabase corta en 1000 filas por default si no se pagina explícitamente, y
 * ya tenemos tablas (repo_productos, repo_calculo_semanal) por encima de eso. */
async function selectAll(
  table: string,
  build: (q: QueryBuilder) => QueryBuilder,
  pageSize = 1000,
): Promise<Record<string, unknown>[]> {
  let from = 0;
  const all: Record<string, unknown>[] = [];
  while (true) {
    const { data, error } = await build(supabase.from(table).select("*")).range(from, from + pageSize - 1);
    if (error) throw error;
    all.push(...(data ?? []));
    if (!data || data.length < pageSize) break;
    from += pageSize;
  }
  return all;
}

async function latestCalculoPorSku() {
  const data = await selectAll("repo_calculo_semanal", (q) => q.order("semana_iso", { ascending: false }));
  const bySku = new Map<string, Record<string, unknown>>();
  for (const row of data) {
    if (!bySku.has(row.sku as string)) bySku.set(row.sku as string, row);
  }
  return bySku;
}

async function latestSnapshotPorSku() {
  const data = await selectAll("repo_stock_snapshot", (q) => q.order("fecha", { ascending: false }));
  const bySku = new Map<string, Record<string, unknown>>();
  for (const row of data) {
    if (!bySku.has(row.sku as string)) bySku.set(row.sku as string, row);
  }
  return bySku;
}

async function pedidosAbiertosPorSku() {
  const data = await selectAll("repo_pedido_items", (q) => q.eq("recibido_completo", false));
  const bySku = new Map<string, Set<string>>();
  for (const row of data) {
    const set = bySku.get(row.sku as string) ?? new Set<string>();
    set.add(row.destino as string);
    bySku.set(row.sku as string, set);
  }
  return bySku;
}

// ─── Rutas de lectura ───────────────────────────────────────────────────────

async function getProductos(url: URL) {
  const soloDescontinuados = url.searchParams.get("descontinuados") === "true";
  const proveedorFiltro = url.searchParams.get("proveedor");

  const productos = await selectAll("repo_productos", (q) =>
    soloDescontinuados ? q.eq("descontinuado", true) : q.eq("descontinuado", false)
  );

  const [calculos, snapshots, abiertos] = await Promise.all([
    latestCalculoPorSku(),
    latestSnapshotPorSku(),
    pedidosAbiertosPorSku(),
  ]);

  let rows = productos.map((p) => {
    const calc = calculos.get(p.sku as string) ?? {};
    const snap = snapshots.get(p.sku as string) ?? {};
    const proveedor = (p.proveedor_manual as string) || (p.proveedor_auto as string) || "Otros";
    const categoria = (p.categoria_manual as string) || (p.categoria_auto as string) || "Bazar";
    return {
      ...p,
      proveedor,
      categoria,
      ...calc,
      stock_deposito_hoy: snap.stock_deposito ?? null,
      stock_full_pret_hoy: snap.stock_full_pret ?? null,
      stock_full_lavan_hoy: snap.stock_full_lavan ?? null,
      stock_actualizado_at: snap.captured_at ?? null,
      destinos_ya_solicitados: Array.from(abiertos.get(p.sku as string) ?? []),
    };
  });

  if (proveedorFiltro) {
    rows = rows.filter((r) => r.proveedor === proveedorFiltro);
  }

  return json(rows);
}

async function getProveedores() {
  const data = await selectAll("repo_productos", (q) => q);
  const set = new Set<string>();
  for (const r of data) {
    set.add((r.proveedor_manual as string) || (r.proveedor_auto as string) || "Otros");
  }
  return json(Array.from(set).sort());
}

async function getPedidos() {
  const pedidos = await selectAll("repo_pedidos", (q) => q.order("creado_at", { ascending: false }));
  const items = await selectAll("repo_pedido_items", (q) => q);

  const result = pedidos.map((p) => {
    const suyos = items.filter((i) => i.pedido_id === p.id);
    const recibidos = suyos.filter((i) => i.recibido_completo).length;
    return { ...p, total_items: suyos.length, items_recibidos: recibidos, items: suyos };
  });
  return json(result);
}

async function getQuiebres(url: URL) {
  const sku = url.searchParams.get("sku");
  const data = await selectAll("repo_quiebre_historial", (q) => {
    q = q.order("fecha_quiebre", { ascending: false });
    return sku ? q.eq("sku", sku) : q;
  });
  return json(data);
}

async function getStockHistorial(url: URL) {
  const sku = url.searchParams.get("sku");
  if (!sku) return json({ error: "Falta ?sku=" }, 400);
  const { data, error } = await supabase
    .from("repo_stock_snapshot")
    .select("fecha, stock_deposito, stock_full_pret, stock_full_lavan")
    .eq("sku", sku)
    .order("fecha", { ascending: true });
  if (error) throw error;
  return json(data);
}

// ─── Rutas de escritura ─────────────────────────────────────────────────────

async function postProveedor(sku: string, req: Request) {
  const { proveedor } = await req.json();
  const { error } = await supabase
    .from("repo_productos")
    .update({ proveedor_manual: proveedor, updated_at: new Date().toISOString() })
    .eq("sku", sku);
  if (error) throw error;
  return json({ ok: true });
}

async function postCategoria(sku: string, req: Request) {
  const { categoria } = await req.json();
  const { error } = await supabase
    .from("repo_productos")
    .update({ categoria_manual: categoria, updated_at: new Date().toISOString() })
    .eq("sku", sku);
  if (error) throw error;
  return json({ ok: true });
}

async function postDescontinuar(sku: string, req: Request) {
  const { descontinuado, por } = await req.json();
  const body = descontinuado
    ? { descontinuado: true, descontinuado_at: new Date().toISOString(), descontinuado_por: por ?? null }
    : { descontinuado: false, descontinuado_at: null, descontinuado_por: null };
  const { error } = await supabase.from("repo_productos").update(body).eq("sku", sku);
  if (error) throw error;
  return json({ ok: true });
}

async function postPedido(req: Request) {
  const body = await req.json();
  const { proveedor, creado_por, notas, items } = body as {
    proveedor: string;
    creado_por: string;
    notas?: string;
    items: { sku: string; destino: string; cantidad_pedida: number; cantidad_sugerida?: number }[];
  };
  if (!proveedor || !creado_por || !items?.length) {
    return json({ error: "Faltan proveedor, creado_por o items" }, 400);
  }

  const { data: pedido, error: pedidoError } = await supabase
    .from("repo_pedidos")
    .insert({ proveedor, creado_por, notas: notas ?? null })
    .select()
    .single();
  if (pedidoError) throw pedidoError;

  const snapshots = await latestSnapshotPorSku();
  const columnaPorDestino: Record<string, string> = {
    deposito: "stock_deposito",
    full_pret: "stock_full_pret",
    full_lavan: "stock_full_lavan",
  };
  const filas = items.map((it) => {
    const snap = snapshots.get(it.sku) ?? {};
    const col = columnaPorDestino[it.destino];
    return {
      pedido_id: pedido.id,
      sku: it.sku,
      destino: it.destino,
      cantidad_pedida: it.cantidad_pedida,
      cantidad_sugerida: it.cantidad_sugerida ?? null,
      stock_al_pedir: col ? (snap[col] as number ?? 0) : 0,
    };
  });

  const { error: itemsError } = await supabase.from("repo_pedido_items").insert(filas);
  if (itemsError) throw itemsError;

  return json({ ok: true, pedido_id: pedido.id });
}

async function postCancelarPedido(id: string, req: Request) {
  const { por } = await req.json().catch(() => ({}));
  const { data: pedido, error: getError } = await supabase
    .from("repo_pedidos").select("estado").eq("id", id).maybeSingle();
  if (getError) throw getError;
  if (!pedido) return json({ error: "Pedido no encontrado" }, 404);
  if (pedido.estado === "recibido_completo") {
    return json({ error: "No se puede cancelar un pedido ya recibido completo" }, 400);
  }
  if (pedido.estado === "cancelado") {
    return json({ error: "El pedido ya está cancelado" }, 400);
  }
  const { error } = await supabase.from("repo_pedidos").update({
    estado: "cancelado",
    cancelado_at: new Date().toISOString(),
    cancelado_por: por ?? null,
  }).eq("id", id);
  if (error) throw error;
  return json({ ok: true });
}

async function patchPedidoItems(id: string, req: Request) {
  const { items } = await req.json() as {
    items: { id: number; cantidad_pedida: number }[];
  };
  if (!items?.length) return json({ error: "Falta items" }, 400);

  const { data: pedido, error: getError } = await supabase
    .from("repo_pedidos").select("estado").eq("id", id).maybeSingle();
  if (getError) throw getError;
  if (!pedido) return json({ error: "Pedido no encontrado" }, 404);
  if (pedido.estado !== "enviado") {
    return json({ error: "Solo se pueden editar cantidades de un pedido en estado 'enviado'" }, 400);
  }

  for (const it of items) {
    if (!(it.cantidad_pedida > 0)) continue;
    const { error } = await supabase.from("repo_pedido_items")
      .update({ cantidad_pedida: it.cantidad_pedida })
      .eq("id", it.id).eq("pedido_id", id);
    if (error) throw error;
  }
  return json({ ok: true });
}

// ─── Router ─────────────────────────────────────────────────────────────────

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS_HEADERS });

  if (!checkPassword(req)) return json({ error: "Contraseña inválida" }, 401);

  const url = new URL(req.url);
  // El path real (sin el prefijo /reposicion-api que agrega Supabase)
  const path = url.pathname.replace(/^\/reposicion-api/, "") || "/";
  const parts = path.split("/").filter(Boolean);

  try {
    if (req.method === "GET" && path === "/productos") return await getProductos(url);
    if (req.method === "GET" && path === "/proveedores") return await getProveedores();
    if (req.method === "GET" && path === "/pedidos") return await getPedidos();
    if (req.method === "GET" && path === "/quiebres") return await getQuiebres(url);
    if (req.method === "GET" && path === "/stock-historial") return await getStockHistorial(url);

    if (req.method === "POST" && parts[0] === "productos" && parts[2] === "proveedor") {
      return await postProveedor(decodeURIComponent(parts[1]), req);
    }
    if (req.method === "POST" && parts[0] === "productos" && parts[2] === "categoria") {
      return await postCategoria(decodeURIComponent(parts[1]), req);
    }
    if (req.method === "POST" && parts[0] === "productos" && parts[2] === "descontinuar") {
      return await postDescontinuar(decodeURIComponent(parts[1]), req);
    }
    if (req.method === "POST" && path === "/pedidos") return await postPedido(req);
    if (req.method === "POST" && parts[0] === "pedidos" && parts[2] === "cancelar") {
      return await postCancelarPedido(decodeURIComponent(parts[1]), req);
    }
    if (req.method === "PATCH" && parts[0] === "pedidos" && parts[2] === "items") {
      return await patchPedidoItems(decodeURIComponent(parts[1]), req);
    }

    return json({ error: "Ruta no encontrada" }, 404);
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
