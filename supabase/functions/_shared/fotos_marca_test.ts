// Pruebas del detector de fotos de marca. Unitarias sin red + una prueba contra fotos reales de ML (necesita red:
// `deno test -A fotos_marca_test.ts`). Las fotos reales son públicas (mlstatic) y salen de MLA2142920094 de Pret.
import { assert, assertEquals } from "jsr:@std/assert@1";
import { dhash, distancia, esFotoDeMarca, sinFotosDeMarca } from "./fotos_marca.ts";

Deno.test("distancia cuenta bits distintos", () => {
  assertEquals(distancia(0b1011n, 0b0001n), 2);
  assertEquals(distancia(0n, 0n), 0);
});

Deno.test("dhash: imagen que crece a la derecha da todos ceros; que decrece, todos unos", () => {
  const w = 40, h = 40, sube = new Float64Array(w * h), baja = new Float64Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { sube[y * w + x] = x; baja[y * w + x] = w - x; }
  assertEquals(dhash(sube, w, h), 0n);
  assertEquals(distancia(dhash(baja, w, h), 0n), 256);
});

// Fotos de marca de otros colores de la misma publicación (otras copias, otros ids) y fotos de producto.
const DE_MARCA = ["844117-MLA88358706751_072025", "748247-MLA88357984465_072025", "750608-MLA112993944182_072026",
  "715869-MLA114209650809_072026", "883477-MLA112452879306_062026", "684632-MLA113617014157_062026"];
const DE_PRODUCTO = ["659162-MLA112993942628_072026", "844362-MLA112601885734_062026", "868068-MLA113781906761_062026",
  "629561-MLA112993972088_072026", "847934-MLA113951812807_062026", "781318-MLA114209739391_072026"];

Deno.test({ name: "fotos reales: reconoce las de marca y deja pasar las de producto", sanitizeResources: false, async fn() {
  for (const id of DE_MARCA) assertEquals(await esFotoDeMarca(id), true, id);
  for (const id of DE_PRODUCTO) assertEquals(await esFotoDeMarca(id), false, id);
  const r = await sinFotosDeMarca([...DE_PRODUCTO.slice(0, 2), DE_MARCA[0], "no-existe-123"]);
  assertEquals(r.quedan, DE_PRODUCTO.slice(0, 2));
  assert(r.fuera.includes(DE_MARCA[0]) && r.fuera.includes("no-existe-123")); // la que no se pudo revisar no se copia
} });
