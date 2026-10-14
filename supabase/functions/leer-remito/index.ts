// supabase/functions/leer-remito/index.ts
//
// Lee con IA (Claude) las fotos de un remito de proveedor y devuelve los datos estructurados.
// La llama la pantalla de recepción (reposicion/dashboard/proveedores/recibir.html) con el
// token del usuario logueado (Supabase Auth). verify_jwt queda en true (default): la plataforma
// rechaza requests sin sesión; además acá se exige que el usuario tenga rol en cc_perfiles.
// Secrets: ANTHROPIC_API_KEY (obligatorio), ANTHROPIC_MODEL (opcional).

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
};
const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { ...CORS, "Content-Type": "application/json" } });

const TOOL = {
  name: "registrar_remito",
  description: "Registra los datos leídos de un remito de proveedor.",
  input_schema: {
    type: "object",
    properties: {
      proveedor_detectado: { type: ["string", "null"], description: "Emisor que figura en el remito" },
      nro_remito: { type: ["string", "null"], description: "Número de remito completo tal cual figura (ej. 0003-00012345)" },
      fecha: { type: ["string", "null"], description: "Fecha en formato YYYY-MM-DD" },
      bultos: { type: ["integer", "null"], description: "Cantidad de bultos si figura" },
      items: {
        type: "array",
        items: {
          type: "object",
          properties: {
            codigo: { type: ["string", "null"], description: "Código/artículo del proveedor tal cual" },
            descripcion: { type: "string", description: "Descripción tal cual figura" },
            cantidad: { type: "number", description: "Unidades de esa línea" },
          },
          required: ["descripcion", "cantidad"],
        },
      },
      observaciones: { type: ["string", "null"], description: "Notas relevantes o partes ilegibles" },
    },
    required: ["items"],
  },
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "Método no permitido" }, 405);

  const auth = req.headers.get("authorization") ?? "";
  const url = Deno.env.get("SUPABASE_URL")!;
  const anon = Deno.env.get("SUPABASE_ANON_KEY")!;
  const perfil = await fetch(`${url}/rest/v1/cc_perfiles?select=rol`, { headers: { apikey: anon, Authorization: auth } })
    .then((r) => (r.ok ? r.json() : []))
    .catch(() => []);
  if (!Array.isArray(perfil) || perfil.length === 0) return json({ error: "Usuario sin permiso" }, 403);

  const key = Deno.env.get("ANTHROPIC_API_KEY");
  if (!key) return json({ error: "Falta el secret ANTHROPIC_API_KEY en Supabase" }, 500);

  let body: { images?: string[]; proveedor?: string };
  try { body = await req.json(); } catch { return json({ error: "Body inválido" }, 400); }
  const images = body.images ?? [];
  if (images.length < 1 || images.length > 6) return json({ error: "Mandá entre 1 y 6 fotos" }, 400);

  const prompt =
    `Estas fotos son las páginas de UN remito de un proveedor textil argentino${body.proveedor ? ` (esperamos que sea de ${body.proveedor})` : ""}.\n` +
    `Extraé el número de remito, la fecha, los bultos y TODAS las líneas de mercadería con su código, descripción y cantidad.\n` +
    `Reglas: ignorá precios e importes; no inventes nada que no se lea (si algo es ilegible dejalo en null o avisalo en observaciones); ` +
    `si una misma línea se repite en varias páginas no la dupliques; las cantidades son unidades, no bultos. ` +
    `Fechas en formato argentino DD/MM/AAAA -> devolvelas como YYYY-MM-DD.`;

  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json" },
    body: JSON.stringify({
      model: Deno.env.get("ANTHROPIC_MODEL") ?? "claude-sonnet-5-5",
      max_tokens: 4096,
      tools: [TOOL],
      tool_choice: { type: "tool", name: "registrar_remito" },
      messages: [{
        role: "user",
        content: [
          ...images.map((data) => ({ type: "image", source: { type: "base64", media_type: "image/jpeg", data } })),
          { type: "text", text: prompt },
        ],
      }],
    }),
  });
  const out = await r.json().catch(() => ({}));
  if (!r.ok) return json({ error: out?.error?.message ?? "Error consultando a la IA" }, 502);
  const bloque = (out.content ?? []).find((c: { type: string }) => c.type === "tool_use");
  if (!bloque) return json({ error: "No pude leer el remito" }, 502);
  return json(bloque.input);
});
