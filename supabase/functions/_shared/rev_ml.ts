// supabase/functions/_shared/rev_ml.ts
//
// Utilidades compartidas por las funciones de revendedores (rev-sync, rev-vincular, y las que vengan) para hablar con
// Mercado Libre EN NOMBRE DE CADA REVENDEDOR con las credenciales que él autorizó (tabla rev_revendedores).
// Credenciales de la aplicación de ML: secrets ML_REV_CLIENT_ID y ML_REV_CLIENT_SECRET.

import type { SupabaseClient } from "jsr:@supabase/supabase-js@2";

export const ML_API = "https://api.mercadolibre.com";
// Nombre propio para identificar estas llamadas (sin datos personales).
export const USER_AGENT = "PretaHome-Revendedores/1.0";

export interface Revendedor {
  id: number;
  ml_user_id: number;
  nickname: string | null;
  access_token: string | null;
  refresh_token: string | null;
  token_expires_at: string | null;
  estado: string;
  conectado_at?: string | null;
}

export const COLUMNAS_REVENDEDOR = "id,ml_user_id,nickname,access_token,refresh_token,token_expires_at,estado,conectado_at";

export const dormir = (ms: number) => new Promise((r) => setTimeout(r, ms));

const MARGEN_VENCIMIENTO_MS = 5 * 60 * 1000;

function vigente(access: string | null, venceIso: string | null): boolean {
  const vence = venceIso ? Date.parse(venceIso) : 0;
  return Boolean(access) && vence - Date.now() > MARGEN_VENCIMIENTO_MS;
}

/** Token de ML vigente de un revendedor; si venció lo renueva.
 *  ML ROTA el refresh_token en cada renovación: el nuevo se guarda enseguida y el anterior deja de servir. Si dos procesos
 *  renuevan a la vez, el segundo recibe `invalid_grant` aunque la cuenta esté bien: por eso, antes de dar la cuenta por
 *  desconectada se vuelve a leer la fila, y si otro proceso ya guardó credenciales nuevas se usan esas. */
export async function tokenValido(supabase: SupabaseClient, rev: Revendedor): Promise<string | null> {
  if (vigente(rev.access_token, rev.token_expires_at)) return rev.access_token;
  const clientId = Deno.env.get("ML_REV_CLIENT_ID") ?? "";
  const clientSecret = Deno.env.get("ML_REV_CLIENT_SECRET") ?? "";
  if (!rev.refresh_token || !clientId || !clientSecret) return null;

  const r = await fetch(`${ML_API}/oauth/token`, {
    method: "POST",
    headers: { "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      client_id: clientId,
      client_secret: clientSecret,
      refresh_token: rev.refresh_token,
    }),
  });

  if (!r.ok) {
    let motivo = "";
    try {
      motivo = String((await r.json()).error ?? "");
    } catch { /* sin cuerpo */ }
    console.error(`refresh ML ${rev.ml_user_id} -> ${r.status} ${motivo}`);
    if (r.status === 400 && motivo === "invalid_grant") {
      const { data } = await supabase.from("rev_revendedores").select(COLUMNAS_REVENDEDOR).eq("id", rev.id).maybeSingle();
      const actual = data as Revendedor | null;
      if (actual && actual.refresh_token !== rev.refresh_token && vigente(actual.access_token, actual.token_expires_at)) {
        return actual.access_token; // otro proceso ya renovó: no es una desconexión
      }
      await supabase.from("rev_revendedores").update({ estado: "desconectado", actualizado_at: new Date().toISOString() }).eq("id", rev.id);
    }
    return null;
  }

  const t = await r.json();
  const { error } = await supabase.from("rev_revendedores").update({
    access_token: t.access_token,
    refresh_token: t.refresh_token,
    token_expires_at: new Date(Date.now() + (t.expires_in ?? 21600) * 1000).toISOString(),
    actualizado_at: new Date().toISOString(),
  }).eq("id", rev.id);
  if (error) console.error(`no se pudo guardar el token renovado de ${rev.ml_user_id}: ${error.message}`);
  return t.access_token;
}

/** GET a la API de ML con el token del revendedor. Reintenta ante 429/5xx. Devuelve el estado y el cuerpo (null si falla). */
export async function mlGet<T = unknown>(token: string, path: string): Promise<{ status: number; body: T | null }> {
  for (let intento = 0; intento < 3; intento++) {
    const r = await fetch(`${ML_API}${path}`, { headers: { Authorization: `Bearer ${token}`, Accept: "application/json", "User-Agent": USER_AGENT } });
    if (r.status === 429 || r.status >= 500) {
      await dormir(1000 * (intento + 1));
      continue;
    }
    if (!r.ok) {
      console.error(`ML GET ${path.split("?")[0]} -> ${r.status}`);
      return { status: r.status, body: null };
    }
    return { status: r.status, body: await r.json() as T };
  }
  return { status: 0, body: null };
}

/** Cambia la cantidad disponible de una publicación (o de una variación) de un revendedor. */
export async function ponerStockML(token: string, itemId: string, variationId: number | null, cantidad: number): Promise<boolean> {
  const url = variationId ? `${ML_API}/items/${itemId}/variations/${variationId}` : `${ML_API}/items/${itemId}`;
  for (let intento = 0; intento < 3; intento++) {
    const r = await fetch(url, {
      method: "PUT",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ available_quantity: cantidad }),
    });
    if (r.ok) return true;
    if (r.status === 429 || r.status >= 500) {
      await dormir(1000 * (intento + 1));
      continue;
    }
    console.error(`ML PUT ${itemId} -> ${r.status}`);
    return false;
  }
  return false;
}
