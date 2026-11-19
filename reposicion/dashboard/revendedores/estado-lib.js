// reposicion/dashboard/revendedores/estado-lib.js
// Lógica pura (sin DOM ni red) de la pantalla "Estado del sistema": nombres legibles de los procesos, cuánto atraso se tolera
// en cada uno y qué alertas mostrar. La pantalla (estado.html) la usa; las pruebas están en reposicion/tests/js/.

// Cada proceso programado: cómo se llama para el dueño, cada cuánto corre y cuánto atraso se tolera antes de alertar.
export const TRABAJOS = {
  'rev-sync-5min': { nombre: 'Stock hacia revendedores', cada: 'cada 5 minutos', limiteMin: 20 },
  'rev-vincular-hora': { nombre: 'Vinculación de publicaciones', cada: 'cada hora', limiteMin: 150 },
  'rev-ordenes-minuto': { nombre: 'Registro de ventas', cada: 'cada minuto', limiteMin: 10 },
  'rev-conciliar-6h': { nombre: 'Conciliación de ventas', cada: 'cada 6 horas', limiteMin: 480 },
};

// Resultado que cada proceso deja guardado (rev_sync_estado) y los valores que significan "salió bien".
const PROCESOS = {
  stock: { nombre: 'La sincronización de stock', ok: ['sombra_ok', 'activo_ok'] },
  vinculacion: { nombre: 'La vinculación de publicaciones', ok: ['ok'] },
  conciliacion: { nombre: 'La conciliación de ventas', ok: ['ok'] },
};

const LECTURA_STOCK_VIEJA_MIN = 20; // el stock de Ecomm-App se lee cada 5 minutos
const TOKEN_SIN_RENOVAR_MIN = 90; // las credenciales se renuevan cada hora: más de 90 min vencida = no se está renovando

export const minutosEntre = (desdeIso, hastaIso) => (Date.parse(hastaIso) - Date.parse(desdeIso)) / 60000;

/** "hace 3 minutos", "hace 2 horas", "hace 1 día". */
export function hace(min) {
  if (!Number.isFinite(min)) return 'nunca';
  const m = Math.max(0, Math.round(min));
  if (m < 1) return 'hace menos de 1 minuto';
  if (m < 60) return `hace ${m} ${m === 1 ? 'minuto' : 'minutos'}`;
  const h = Math.round(m / 60);
  if (h < 48) return `hace ${h} ${h === 1 ? 'hora' : 'horas'}`;
  const d = Math.round(h / 24);
  return `hace ${d} días`;
}

/** Alertas a mostrar, las rojas primero. Cada una: { nivel: 'rojo' | 'amarillo', titulo, detalle }. Sin alertas = todo en orden. */
export function construirAlertas(d) {
  const out = [];
  const ahora = d.ahora;

  for (const t of d.trabajos ?? []) {
    const meta = TRABAJOS[t.nombre];
    const nombre = meta?.nombre ?? t.nombre;
    if (!t.activo) {
      out.push({ nivel: 'rojo', titulo: `${nombre} está apagado`, detalle: 'El trabajo programado figura inactivo.' });
    } else if (!t.ultima_vez) {
      out.push({ nivel: 'amarillo', titulo: `${nombre} todavía no corrió solo`, detalle: 'Todavía no hay ninguna corrida programada registrada. Es normal si se programó hace poco.' });
    } else {
      const min = minutosEntre(t.ultima_vez, ahora);
      if (meta && min > meta.limiteMin) {
        out.push({ nivel: 'rojo', titulo: `${nombre} está atrasado`, detalle: `Corrió por última vez ${hace(min)} y tendría que correr ${meta.cada}.` });
      } else if (t.estado && t.estado !== 'succeeded') {
        out.push({ nivel: 'rojo', titulo: `${nombre} falló en su última corrida`, detalle: `Estado: ${t.estado}.` });
      }
    }
  }

  for (const [clave, meta] of Object.entries(PROCESOS)) {
    const p = d.procesos?.[clave];
    if (p && !meta.ok.includes(p.resultado)) {
      out.push({ nivel: 'rojo', titulo: `${meta.nombre} terminó con problemas`, detalle: `Resultado de la última corrida: ${p.resultado}.` });
    }
  }

  if (d.oferta?.ultima_lectura && (d.catalogo ?? 0) > 0) {
    const min = minutosEntre(d.oferta.ultima_lectura, ahora);
    if (min > LECTURA_STOCK_VIEJA_MIN) {
      out.push({ nivel: 'rojo', titulo: 'El stock de Ecomm-App está desactualizado', detalle: `La última lectura fue ${hace(min)}: los revendedores podrían ver stock viejo.` });
    }
  }

  for (const r of d.revendedores ?? []) {
    const nombre = r.nickname || `Revendedor ${r.id}`;
    if (r.estado === 'desconectado') {
      out.push({ nivel: 'rojo', titulo: `${nombre} se desconectó`, detalle: 'Mercado Libre rechazó su credencial: tiene que volver a conectar su cuenta.' });
    } else if (r.estado === 'pausado') {
      out.push({ nivel: 'amarillo', titulo: `${nombre} está pausado`, detalle: 'No se sincroniza su stock ni se registran sus ventas.' });
    } else if (r.token_vence && minutosEntre(r.token_vence, ahora) > TOKEN_SIN_RENOVAR_MIN) {
      out.push({ nivel: 'rojo', titulo: `La credencial de ${nombre} venció y no se renueva`, detalle: 'Puede dejar de verse su cuenta: conviene revisar la vinculación.' });
    }
    if (r.activas_sin_reconocer > 0) {
      out.push({
        nivel: 'amarillo',
        titulo: `${nombre}: ${r.activas_sin_reconocer} ${r.activas_sin_reconocer === 1 ? 'publicación activa' : 'publicaciones activas'} sin SKU reconocido`,
        detalle: 'No se les sincroniza el stock. Puede vincularlas a mano desde "Mis publicaciones".',
      });
    }
  }

  const noRec = d.ventas?.no_reconocidas ?? 0;
  if (noRec > 0) {
    out.push({ nivel: 'amarillo', titulo: `${noRec} ${noRec === 1 ? 'venta' : 'ventas'} con SKU no reconocido`, detalle: 'Se registraron igual, pero sin precio mayorista hasta que se reconozca el SKU.' });
  }

  const bajo = d.bajo_minimo?.length ?? 0;
  if (bajo > 0) {
    out.push({ nivel: 'rojo', titulo: `${bajo} ${bajo === 1 ? 'publicación' : 'publicaciones'} por debajo del precio mínimo`, detalle: 'Solo se avisa (decisión del dueño): hablá con el revendedor. El detalle está más abajo.' });
  }

  return out.sort((a, b) => (a.nivel === b.nivel ? 0 : a.nivel === 'rojo' ? -1 : 1));
}
