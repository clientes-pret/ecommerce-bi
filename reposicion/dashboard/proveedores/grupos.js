// Segmentación de productos: categoría → grupo (modelo sin la medida) → medida → color/SKU.
// El grupo y la medida salen del nombre del producto en Tiendanube (cc_productos.medida está vacía); en algunos
// productos la medida viene en la variante ("color"), así que también se mira ahí.
// Sin dependencias: lo usan el panel y las pruebas (node reposicion/tests/js/grupos_test.mjs).

// De lo más específico a lo más general: "2 1/2 plazas" antes que "2 plazas", "super king" antes que "king".
const CAMAS = [
  ['Queen', /\b2\s*(?:1\/2|½|y\s+media)\s*plazas?\b/i],
  ['Twin', /\b1\s*(?:1\/2|½|y\s+media)\s*(?:plazas?\b)?/i],
  ['Super King', /\bsuper\s*king\b(?:\s*size\b)?/i],
  ['King', /\bking\b(?:\s*size\b)?/i],
  ['Queen', /\bqueen\b(?:\s*size\b)?/i],
  ['Twin', /\btwin\b(?:\s*size\b)?/i],
  ['Full', /\bfull\b(?:\s*size\b)?(?!\s*color)/i],
  ['Full', /\b2\s*plazas?\b/i],
  ['1 plaza', /\b1\s*plaza\b/i],
];
const ORDEN_CAMA = ['1 plaza', 'Twin', 'Full', 'Queen', 'King', 'Super King'];
const MEDIDA_CM = /\b(\d{2,3}(?:[.,]\d)?)\s*(?:cm\s*)?x\s*(\d{2,3}(?:[.,]\d)?)(?:\s*(?:cms?|mts?)\b\.?)?/i;

function sacarMedida(texto) {
  let resto = String(texto || ''), cama = null;
  // Puede decir las dos cosas ("1 1/2 plaza / Twin"): se sacan todas y manda la primera de la lista.
  for (const [nombre, re] of CAMAS) {
    while (re.test(resto)) { cama = cama || nombre; resto = resto.replace(re, ' '); }
  }
  let cm = null;
  const m = resto.match(MEDIDA_CM);
  if (m) { cm = `${m[1]}x${m[2]}`; resto = resto.replace(MEDIDA_CM, ' '); }
  return { medida: cama || cm, resto };
}

const limpiar = (s) => s
  .replace(/\(\s*[-/]*\s*\)/g, ' ')
  .replace(/\bsize\b/gi, ' ')
  .replace(/\s+/g, ' ')
  .replace(/(?:\s*[-–/|,]\s*){2,}/g, ' - ')
  .replace(/^[\s\-–/|,]+|[\s\-–/|,]+$/g, '')
  .trim();

export function grupoYMedida(nombre, color) {
  const delNombre = sacarMedida(nombre);
  const delColor = sacarMedida(color);
  const grupo = limpiar(delNombre.resto.replace(/\s*-\s*pret(?:\s+a\s+home)?\b/gi, ' ')) || limpiar(String(nombre || ''));
  return {
    grupo,
    medida: delNombre.medida || delColor.medida || '',
    color: delColor.medida ? limpiar(delColor.resto) : String(color || '').trim(),
  };
}

export function ordenMedida(a, b) {
  const clave = (m) => {
    if (!m) return [2, 0, 0];
    const i = ORDEN_CAMA.indexOf(m);
    if (i >= 0) return [0, i, 0];
    const [x, y] = m.split('x').map((v) => parseFloat(v.replace(',', '.')) || 0);
    return [1, x, y];
  };
  const ka = clave(a), kb = clave(b);
  return ka[0] - kb[0] || ka[1] - kb[1] || ka[2] - kb[2];
}

// Árbol [{categoria, grupos: [{grupo, medidas: [{medida, filas}], filas}], filas}] ordenado alfabéticamente.
// Cada fila de entrada necesita: categoria, nombre, color, sku.
export function segmentar(filas) {
  const cats = new Map();
  for (const f of filas) {
    const g = grupoYMedida(f.nombre, f.color);
    const fila = { ...f, grupo: g.grupo, medida: g.medida, colorLimpio: g.color };
    if (!cats.has(fila.categoria)) cats.set(fila.categoria, new Map());
    const grupos = cats.get(fila.categoria), k = g.grupo.toLowerCase();
    if (!grupos.has(k)) grupos.set(k, { grupo: g.grupo, filas: [] });
    grupos.get(k).filas.push(fila);
  }
  const alfa = (a, b) => String(a).localeCompare(String(b), 'es', { numeric: true });
  return [...cats.entries()].sort((a, b) => alfa(a[0], b[0])).map(([categoria, grupos]) => {
    const lista = [...grupos.values()].sort((a, b) => alfa(a.grupo, b.grupo)).map((g) => {
      const medidas = [...new Set(g.filas.map((f) => f.medida))].sort(ordenMedida).map((medida) => ({
        medida,
        filas: g.filas.filter((f) => f.medida === medida).sort((a, b) => alfa(a.colorLimpio, b.colorLimpio) || alfa(a.sku, b.sku)),
      }));
      return { grupo: g.grupo, medidas, filas: medidas.flatMap((m) => m.filas) };
    });
    return { categoria, grupos: lista, filas: lista.flatMap((g) => g.filas) };
  });
}
