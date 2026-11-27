// supabase/functions/proveedor-portal/index.ts
//
// Link público para proveedores (reposicion/dashboard/portal/). Lo abre el proveedor sin usuario ni contraseña: lo
// que autoriza es el código secreto del link (prov_links.token), que se puede dar de baja desde el panel.
// Usa la clave de servicio solo para llamar a prov_portal_datos / prov_portal_responder, que devuelven únicamente lo
// pendiente de ese proveedor (sin stock, ventas ni costos).

import { createClient } from "jsr:@supabase/supabase-js@2";
import { atender } from "./logic.ts";

const supabase = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

Deno.serve((req) => atender(req, async (fn, args) => await supabase.rpc(fn, args)));
