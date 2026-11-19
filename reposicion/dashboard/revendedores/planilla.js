// reposicion/dashboard/revendedores/planilla.js — arma el Excel de productos elegidos para subir a Mercado Libre.
//
// LIMITACIÓN (verificada 17/11/2026): Mercado Libre no tiene un formato único y público de carga masiva. Su planilla
// ("Publicaciones → Publicar → Masivamente") se descarga desde la cuenta del vendedor, es distinta por categoría y no se puede
// armar de afuera. Por eso esta planilla trae TODO lo necesario por producto (título, descripción, fotos, SKU, precio, stock,
// precio mínimo) con columnas claras para copiar/pegar en la planilla de ML de cada categoría. Publicar directo por API es la Fase 3.
export const LARGO_TITULO_ML = 60;

// Título de ML: máximo 60 caracteres, cortado en una palabra entera.
export function tituloML(t) {
  const s = String(t || '').replace(/\s+/g, ' ').trim();
  if (s.length <= LARGO_TITULO_ML) return s;
  const corte = s.slice(0, LARGO_TITULO_ML + 1);
  const i = corte.lastIndexOf(' ');
  return (i >= 30 ? corte.slice(0, i) : s.slice(0, LARGO_TITULO_ML)).replace(/[\s\-–,.]+$/, '');
}

// Precio de venta que se sugiere en la planilla: el de lista de Pret a Home, nunca por debajo del mínimo.
export function precioPlanilla(p) {
  const base = p.precio_sugerido != null ? Number(p.precio_sugerido) : null;
  const min = p.precio_minimo != null ? Number(p.precio_minimo) : null;
  if (base == null) return min;
  return min != null ? Math.max(base, min) : base;
}

const COLUMNAS = [
  ['SKU', 22], ['Título para publicar (máx. 60)', 46], ['Descripción', 70], ['Precio de venta sugerido ($)', 18],
  ['Precio mínimo permitido ($)', 18], ['Stock disponible (u.)', 14],
  ['Foto 1', 40], ['Foto 2', 40], ['Foto 3', 40], ['Foto 4', 40], ['Foto 5', 40], ['Foto 6', 40],
  ['Color', 16], ['Medida', 16], ['Categoría', 34], ['Precio mayorista ($)', 16], ['Diferencia estimada ($)', 16], ['Título completo', 60],
];

const INSTRUCCIONES = [
  ['Cómo usar esta planilla'],
  [''],
  ['1. Esta planilla es una lista completa de los productos que elegiste: título, descripción, fotos, SKU, precio y stock.'],
  ['2. Mercado Libre no tiene una planilla única de carga masiva: la suya se descarga desde tu cuenta (Publicaciones → Publicar → Masivamente),'],
  ['   es distinta para cada categoría y no se puede armar desde afuera. Copiá y pegá las columnas de esta hoja en la planilla de tu categoría.'],
  ['3. SKU: ponelo SIEMPRE en el campo "SKU" / "Código de vendedor" de la publicación (seller_custom_field). Así el sistema reconoce el producto'],
  ['   y mantiene el stock al día en tu publicación. Sin SKU no se sincroniza el stock.'],
  ['4. Fotos: son links públicos. Si la planilla de ML pide fotos por link, pegalos; si no, bajalas y subilas.'],
  ['5. Precio: la columna "Precio de venta sugerido" es el precio de lista de Pret a Home. Podés cambiarlo, pero en los productos con'],
  ['   "Precio mínimo permitido" no publiques por debajo: si lo hacés, se le avisa a Pret a Home.'],
  ['6. "Precio mayorista" es lo que le pagás a Pret a Home por unidad vendida. "Diferencia estimada" es el precio sugerido menos el mayorista,'],
  ['   ANTES de la comisión de Mercado Libre, envío e impuestos.'],
  ['7. Stock: es lo que se te ofrece hoy. Cambia solo; no hace falta que lo actualices.'],
  ['8. Título: Mercado Libre permite 60 caracteres. Revisalo antes de publicar; el título completo está en la última columna.'],
];

export async function generarPlanilla(productos, nombreRevendedor = '') {
  if (!window.ExcelJS) throw new Error('No cargó la librería de Excel. Revisá tu conexión y recargá la página.');
  const wb = new window.ExcelJS.Workbook();
  wb.creator = 'Pret a Home';
  wb.created = new Date();

  const ws = wb.addWorksheet('Productos', { views: [{ state: 'frozen', xSplit: 2, ySplit: 1 }] });
  ws.columns = COLUMNAS.map(([header, width]) => ({ header, width }));
  const cab = ws.getRow(1);
  cab.font = { bold: true, color: { argb: 'FFFFFFFF' } };
  cab.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF2449B8' } };
  cab.alignment = { vertical: 'middle', wrapText: true };
  cab.height = 34;

  for (const p of productos) {
    const venta = precioPlanilla(p);
    const mayorista = p.precio_mayorista != null ? Number(p.precio_mayorista) : null;
    const fotos = Array.isArray(p.fotos) ? p.fotos : [];
    ws.addRow([
      p.sku, tituloML(p.titulo), p.descripcion || '', venta, p.precio_minimo != null ? Number(p.precio_minimo) : null, Number(p.stock_ofrecido || 0),
      fotos[0] || '', fotos[1] || '', fotos[2] || '', fotos[3] || '', fotos[4] || '', fotos[5] || '',
      p.color || '', p.medida || '', p.categoria || '', mayorista,
      venta != null && mayorista != null ? venta - mayorista : null, p.titulo || '',
    ]);
  }
  const ult = ws.rowCount;
  for (const col of ['D', 'E', 'P', 'Q']) ws.getColumn(col).numFmt = '#,##0';
  for (let r = 2; r <= ult; r++) {
    const fila = ws.getRow(r);
    fila.getCell(1).font = { bold: true };
    fila.alignment = { vertical: 'top' };
    const t = String(fila.getCell(2).value || '');
    if (t.length > LARGO_TITULO_ML) fila.getCell(2).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFDF0E6' } };
    if (fila.getCell(5).value != null) fila.getCell(5).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFF1C2' } };
    // En rojo lo que falta y hay que completar a mano antes de publicar: precio, título, fotos.
    const falta = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFDECEA' } };
    if (fila.getCell(4).value == null) fila.getCell(4).fill = falta;
    if (!fila.getCell(2).value) fila.getCell(2).fill = falta;
    if (!fila.getCell(7).value) fila.getCell(7).fill = falta;
  }
  ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: 1, column: COLUMNAS.length } };

  const wi = wb.addWorksheet('Instrucciones');
  wi.getColumn(1).width = 130;
  INSTRUCCIONES.forEach((f) => wi.addRow(f));
  wi.getRow(1).font = { bold: true, size: 14 };

  const buf = await wb.xlsx.writeBuffer();
  const blob = new Blob([buf], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  const hoy = new Date().toLocaleDateString('en-CA');
  const nombre = `planilla-ml-${String(nombreRevendedor).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'productos'}-${hoy}.xlsx`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = nombre;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  return { nombre, filas: productos.length, bytes: blob.size };
}
