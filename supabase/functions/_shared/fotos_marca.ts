// supabase/functions/_shared/fotos_marca.ts
//
// Fotos institucionales de Pret a Home ("¿Quiénes somos?" y "Preguntas frecuentes") que NUNCA se copian a la publicación
// de un revendedor (pedido del usuario, 18/11/2026). En las publicaciones de Pret cada color las lleva subidas aparte
// (cada copia con otro id), así que se reconocen por la imagen: huella visual dHash de 16x16 = 256 bits sobre la miniatura
// de ML (`D_<id>-I.jpg`). Medido el 19/11/2026: las de marca quedan a pocos bits de las de referencia y cualquier otra foto
// a 90 o más (ver fotos_marca_test.ts). Misma idea que reposicion/prototipos/fotos_marca.py (las huellas no son
// intercambiables: cada lado reduce la imagen con otro método).

import jpeg from "npm:jpeg-js@0.4.4";

// Huellas calculadas con esta misma función sobre las fotos de marca de MLA2142920094 (986791-MLA81997463165_012025 y
// 990935-MLA81997473315_012025). Para sumar otra imagen a excluir: `deno run -A fotos_marca.ts <picture_id>` imprime su huella.
export const HUELLAS: Record<string, bigint> = {
  quienes_somos: 0x1b5f2f5d3e494edd5ad57a757a7546f573353b311a791b9d32872281ccc146c1n,
  preguntas_frecuentes: 0x7b6d177d02fd02fd17e102e507c14ae102e106ed76496a55324f224fcc4d4263n,
};
export const UMBRAL = 30;

/** Huella dHash (n x n bits) de una imagen en escala de grises, reduciéndola por promedio de áreas a (n+1) x n. */
export function dhash(gris: Float64Array, ancho: number, alto: number, n = 16): bigint {
  const W = n + 1, Hh = n;
  const red = new Float64Array(W * Hh);
  for (let y = 0; y < Hh; y++) {
    const y0 = (y * alto) / Hh, y1 = ((y + 1) * alto) / Hh;
    for (let x = 0; x < W; x++) {
      const x0 = (x * ancho) / W, x1 = ((x + 1) * ancho) / W;
      let suma = 0, peso = 0;
      for (let yy = Math.floor(y0); yy < Math.ceil(y1); yy++) {
        const py = Math.min(yy + 1, y1) - Math.max(yy, y0);
        if (py <= 0) continue;
        for (let xx = Math.floor(x0); xx < Math.ceil(x1); xx++) {
          const px = Math.min(xx + 1, x1) - Math.max(xx, x0);
          if (px <= 0) continue;
          suma += gris[yy * ancho + xx] * px * py;
          peso += px * py;
        }
      }
      red[y * W + x] = peso ? suma / peso : 0;
    }
  }
  let bits = 0n;
  for (let y = 0; y < Hh; y++) for (let x = 0; x < n; x++) bits = (bits << 1n) | (red[y * W + x] > red[y * W + x + 1] ? 1n : 0n);
  return bits;
}

export function distancia(a: bigint, b: bigint): number {
  let x = a ^ b, c = 0;
  while (x) { c += Number(x & 1n); x >>= 1n; }
  return c;
}

/** Huella de una foto de ML a partir de los bytes JPEG. */
export function huellaDeJpeg(bytes: Uint8Array): bigint {
  const img = jpeg.decode(bytes, { useTArray: true, formatAsRGBA: true });
  const gris = new Float64Array(img.width * img.height);
  for (let i = 0; i < gris.length; i++) {
    const r = img.data[i * 4], g = img.data[i * 4 + 1], b = img.data[i * 4 + 2];
    gris[i] = 0.299 * r + 0.587 * g + 0.114 * b;
  }
  return dhash(gris, img.width, img.height);
}

export async function huellaDeFoto(pictureId: string): Promise<bigint> {
  const r = await fetch(`https://http2.mlstatic.com/D_${pictureId}-I.jpg`);
  if (!r.ok) throw new Error(`foto ${pictureId}: HTTP ${r.status}`);
  return huellaDeJpeg(new Uint8Array(await r.arrayBuffer()));
}

/** true = foto de marca, false = foto de producto, null = no se pudo revisar. */
export async function esFotoDeMarca(pictureId: string): Promise<boolean | null> {
  try {
    const h = await huellaDeFoto(pictureId);
    return Math.min(...Object.values(HUELLAS).map((v) => distancia(h, v))) <= UMBRAL;
  } catch {
    return null;
  }
}

/** Separa las fotos a copiar de las descartadas. Una foto que no se pudo revisar tampoco se copia (mejor una foto menos
 *  que publicar una de marca). Se revisan en paralelo. */
export async function sinFotosDeMarca(ids: string[]): Promise<{ quedan: string[]; fuera: string[] }> {
  const res = await Promise.all(ids.map((id) => esFotoDeMarca(id)));
  return { quedan: ids.filter((_, i) => res[i] === false), fuera: ids.filter((_, i) => res[i] !== false) };
}

if (import.meta.main) {
  for (const id of Deno.args) console.log(id, "0x" + (await huellaDeFoto(id)).toString(16));
}
