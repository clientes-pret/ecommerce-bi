// Lógica pura de scorecard-costos (sin red ni base), probada en logic_test.ts.

export type VarianteCosto = { sku?: string | null; cost?: number | string | null };

/** Costo por SKU de las variantes de Ecomm-App. Sin SKU o sin costo (> 0) no entra; si un SKU aparece repetido con
 *  costos distintos queda el mayor y se informa. */
export function costosPorSku(variantes: VarianteCosto[]): { costos: Map<string, number>; repetidos: string[] } {
  const costos = new Map<string, number>();
  const repetidos = new Set<string>();
  for (const v of variantes) {
    const sku = String(v.sku ?? "").trim();
    const costo = Math.round(Number(v.cost ?? 0) * 100) / 100;
    if (!sku || !(costo > 0)) continue;
    const antes = costos.get(sku);
    if (antes !== undefined && antes !== costo) repetidos.add(sku);
    costos.set(sku, Math.max(costo, antes ?? 0));
  }
  return { costos, repetidos: [...repetidos].sort() };
}

/** Qué cambiaría respecto de la lista guardada. */
export function comparar(nuevos: Map<string, number>, guardados: Map<string, number>) {
  let agregados = 0;
  const cambiados: Array<{ sku: string; antes: number; ahora: number }> = [];
  for (const [sku, costo] of nuevos) {
    const antes = guardados.get(sku);
    if (antes === undefined) agregados++;
    else if (Math.abs(antes - costo) > 0.005) cambiados.push({ sku, antes, ahora: costo });
  }
  const noVienen = [...guardados.keys()].filter((s) => !nuevos.has(s)).length;
  return { agregados, cambiados, noVienen };
}

/** El rol de una clave de Supabase (JWT). La firma ya la validó la plataforma (verify_jwt = true). */
export function rolDelToken(autorizacion: string | null): string | null {
  const jwt = (autorizacion ?? "").replace(/^Bearer\s+/i, "");
  try {
    const cuerpo = jwt.split(".")[1].replaceAll("-", "+").replaceAll("_", "/");
    return JSON.parse(atob(cuerpo.padEnd(Math.ceil(cuerpo.length / 4) * 4, "="))).role ?? null;
  } catch {
    return null;
  }
}
