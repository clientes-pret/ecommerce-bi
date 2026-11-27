// Quién puede usar la API de Reposición:
//   * un usuario del panel (sesión de Supabase) con rol dueño o administrador, o
//   * la contraseña compartida (header X-App-Password), mientras dure la transición (27/11/2026).

export const ROLES = ["admin", "dueno"];

export type Consultas = {
  /** id del usuario dueño del token, o null si el token no es de una sesión válida. */
  usuario: (token: string) => Promise<string | null>;
  /** rol en cc_perfiles, o null. */
  rol: (id: string) => Promise<string | null>;
};

export async function autorizado(req: Request, consultas: Consultas, password: string): Promise<boolean> {
  const auth = req.headers.get("authorization") ?? "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (token) {
    try {
      const id = await consultas.usuario(token);
      if (id && ROLES.includes((await consultas.rol(id)) ?? "")) return true;
    } catch (e) {
      console.error("No se pudo validar la sesión", e);
    }
  }
  if (!password) return true; // sin password configurado -> no bloquea (setup inicial)
  return req.headers.get("x-app-password") === password;
}
