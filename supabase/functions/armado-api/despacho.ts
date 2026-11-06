// supabase/functions/armado-api/despacho.ts
//
// Parte pura (sin red ni base) del modo Despacho: de qué mensajería es un paquete según su QR, y a qué mensajería
// le toca un pedido de Tiendanube según su opción de envío. Está aparte de index.ts para poder probarla
// (despacho_test.ts) con los códigos reales de las etiquetas.
//
// Qué trae el QR de cada etiqueta (verificado con escaneos reales; Andreani nunca se escaneó, ver abajo):
//   - Zeta: {"did": …}                       Ninja: {"internalID": …}   → grupo zeta_ninja (se registra a cuál de los dos se entrega)
//   - Mercado Libre Flex: {"id","sender_id"} → grupo zeta_ninja
//   - Correo Argentino (Envío Nube): "<seguimiento>|DOM|…" o "…|Sucursal|…"  → grupo correo
//   - Mercado Libre vía Correo: {"carrier_data": "HC…|Sucursal|…", "id", "t"} → grupo correo
//   - Mercado Libre Colecta: {"id","t":"lm"} → lo retira Mercado Libre, no es de ninguna de las tres
//   - Andreani: formato desconocido. Con "Andreani" elegido se acepta cualquier código que ninguna otra
//     mensajería reconozca, y se cuenta por su texto.

export const ZETA_TOKEN_SUFIJO = "d54df4s8a513";

// A quién se le entrega (queda registrado, para poder reclamar un paquete perdido) y qué "grupo" de pendientes comparte:
// Zeta y Ninja se eligen por separado, pero lo que hay que entregar de Flex y Tiendanube es un solo total entre los dos.
export type Mensajeria = "zeta" | "ninja" | "correo" | "andreani";
export const MENSAJERIAS: Mensajeria[] = ["zeta", "ninja", "correo", "andreani"];
export type Pool = "zeta_ninja" | "correo" | "andreani";
export const POOLS: Pool[] = ["zeta_ninja", "correo", "andreani"];
export const poolDe = (m: Mensajeria): Pool => (m === "zeta" || m === "ninja" ? "zeta_ninja" : m);
export const MIEMBROS: Record<Pool, Mensajeria[]> = { zeta_ninja: ["zeta", "ninja"], correo: ["correo"], andreani: ["andreani"] };
export type TipoPaquete = "flex_ml" | "tn" | "otro";

export type Formato =
  | "zeta" | "ninja" | "correo" | "ml_flex" | "ml_correo" | "ml_colecta" | "ml_otro"
  | "ml_venta" | "ml_envio_manual" | "numero_tn" | "desconocido";

export type Paquete = {
  formato: Formato;
  /** De qué grupo de mensajerías es según el código (Zeta y Ninja son un grupo); null si no lo dice (Colecta, desconocido, números a mano). */
  grupo: Pool | null;
  tipo: TipoPaquete | null;
  /** Identifica el paquete para no contarlo dos veces. */
  clave: string | null;
  envio?: string;          // id de envío de Mercado Libre
  sender?: string;         // cuenta ML que dice el QR
  zetaToken?: string;      // token de la página de seguimiento de Zeta
  ref?: string;            // seguimiento / número que se busca en Tiendanube
  venta?: string;          // número de venta de ML escrito a mano
  /** Hay que averiguar a qué mensajería le toca (código escrito a mano: no lo dice solo). */
  resolverGrupo?: boolean;
};

export function detectarPaquete(raw: string): Paquete | null {
  const txt = String(raw ?? "").trim();
  if (!txt) return null;

  if (txt.startsWith("{")) {
    try {
      const o = JSON.parse(txt);
      if (o.did) {
        const did = String(o.did);
        return { formato: "zeta", grupo: "zeta_ninja", tipo: "tn", clave: `zeta|${did}`, zetaToken: `${did}${ZETA_TOKEN_SUFIJO}` };
      }
      if (o.internalID) {
        const id = String(o.internalID);
        return { formato: "ninja", grupo: "zeta_ninja", tipo: "tn", clave: `ninja|${id}`, ref: id };
      }
      if (o.id && /^\d{10,}$/.test(String(o.id))) {
        const envio = String(o.id);
        if (o.carrier_data) return { formato: "ml_correo", grupo: "correo", tipo: "otro", clave: `ml|${envio}`, envio, ref: String(o.carrier_data).split("|")[0] };
        if (o.sender_id) return { formato: "ml_flex", grupo: "zeta_ninja", tipo: "flex_ml", clave: `ml|${envio}`, envio, sender: String(o.sender_id) };
        if (o.t === "lm") return { formato: "ml_colecta", grupo: null, tipo: null, clave: null, envio };
        return { formato: "ml_otro", grupo: null, tipo: null, clave: null, envio };
      }
    } catch { /* no era JSON: sigue abajo */ }
  }

  const tokenZeta = txt.match(/lightdata\.app\/tracking\.php\?token=([\w]+)/);
  if (tokenZeta) {
    const token = tokenZeta[1];
    const did = token.endsWith(ZETA_TOKEN_SUFIJO) ? token.slice(0, -ZETA_TOKEN_SUFIJO.length) : token;
    return { formato: "zeta", grupo: "zeta_ninja", tipo: "tn", clave: `zeta|${did}`, zetaToken: token };
  }

  // Correo Argentino: "<seguimiento>|DOM|…" / "…|Sucursal|…"
  if (txt.includes("|")) {
    const seg = txt.split("|")[0].trim();
    if (seg) return { formato: "correo", grupo: "correo", tipo: "tn", clave: `correo|${seg}`, ref: seg };
  }

  const limpio = txt.replace(/^#/, "").replace(/\s+/g, "");
  if (/^\d+$/.test(limpio)) {
    if (limpio.length >= 15 && limpio.startsWith("2000")) return { formato: "ml_venta", grupo: null, tipo: null, clave: null, venta: limpio, resolverGrupo: true };
    if (limpio.length >= 10) return { formato: "ml_envio_manual", grupo: null, tipo: null, clave: null, envio: limpio, resolverGrupo: true };
    return { formato: "numero_tn", grupo: null, tipo: null, clave: null, ref: limpio, resolverGrupo: true };
  }
  // Cualquier otro texto: puede ser Andreani (se acepta solo si está elegida)
  return { formato: "desconocido", grupo: null, tipo: null, clave: `andreani|${limpio.toLowerCase().slice(0, 200)}`, ref: limpio };
}

/**
 * Mensajería que lleva un pedido de Tiendanube, según el nombre de su opción de envío
 * (fulfillments[0].shipping.option.name, o shipping_option). null = no entra en Despacho
 * (retira en el local, o una opción que no se conoce).
 */
export function grupoDeEnvioTN(nombreOpcion: string | null | undefined, carrier?: string | null): Pool | null {
  const n = `${nombreOpcion ?? ""} ${carrier ?? ""}`.toLowerCase();
  if (!n.trim()) return null;
  if (n.includes("andreani")) return "andreani";
  if (n.includes("correo") || n.includes("oca")) return "correo";
  if (n.includes("ninja") || n.includes("zeta") || n.includes("en el día") || n.includes("en el dia") || n.includes("express")) return "zeta_ninja";
  // "Envío Nube" a secas (punto de retiro sin más detalle) es de Correo Argentino
  if (n.includes("envío nube") || n.includes("envio nube")) return "correo";
  return null;
}

export const NOMBRE_MENSAJERIA: Record<Mensajeria, string> = {
  zeta: "Zeta",
  ninja: "Ninja",
  correo: "Correo Argentino",
  andreani: "Andreani",
};
export const NOMBRE_POOL: Record<Pool, string> = {
  zeta_ninja: "Zeta / Ninja",
  correo: "Correo Argentino",
  andreani: "Andreani",
};

/** Hora (0-23) y día (YYYY-MM-DD) en Argentina de una fecha ISO. */
export function fechaHoraAR(iso: string): { dia: string; hora: number } {
  const d = new Date(iso);
  const dia = d.toLocaleDateString("en-CA", { timeZone: "America/Argentina/Buenos_Aires" });
  const hora = Number(d.toLocaleString("en-GB", { timeZone: "America/Argentina/Buenos_Aires", hour: "2-digit", hour12: false })) % 24;
  return { dia, hora };
}

/** Un Flex de ML creado hoy después de las 14 hs sale mañana: no se cuenta como adeudado hoy. */
export function esParaManana(creadoIso: string, hoy: string, corteHora = 14): boolean {
  const { dia, hora } = fechaHoraAR(creadoIso);
  return dia === hoy && hora >= corteHora;
}
