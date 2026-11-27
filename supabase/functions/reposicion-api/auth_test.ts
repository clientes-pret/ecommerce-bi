// Correr: deno test supabase/functions/reposicion-api/auth_test.ts
import { autorizado, type Consultas } from "./auth.ts";

function igual(a: unknown, b: unknown, msg = "") {
  if (a !== b) throw new Error(`${msg}: esperado ${b}, obtenido ${a}`);
}
const USUARIOS: Record<string, string> = { "t-dueno": "1", "t-admin": "2", "t-marketing": "3", "t-deposito": "4", "t-revendedor": "5" };
const ROL: Record<string, string> = { "1": "dueno", "2": "admin", "3": "marketing", "4": "deposito" };
const consultas: Consultas = {
  usuario: (t) => Promise.resolve(USUARIOS[t] ?? null),
  rol: (id) => Promise.resolve(ROL[id] ?? null),
};
const pedir = (h: Record<string, string>) => new Request("http://x/reposicion-api/productos", { headers: h });
const CLAVE = "clave-de-prueba";

Deno.test("dueño y administrador entran con su sesión", async () => {
  for (const t of ["t-dueno", "t-admin"]) igual(await autorizado(pedir({ Authorization: `Bearer ${t}` }), consultas, CLAVE), true, t);
});

Deno.test("otros roles, revendedores y tokens cualquiera no entran con sesión", async () => {
  for (const t of ["t-marketing", "t-deposito", "t-revendedor", "clave-publica", ""]) {
    igual(await autorizado(pedir({ Authorization: `Bearer ${t}` }), consultas, CLAVE), false, t || "vacío");
  }
  igual(await autorizado(pedir({}), consultas, CLAVE), false, "sin nada");
});

Deno.test("la contraseña compartida sigue sirviendo", async () => {
  igual(await autorizado(pedir({ "X-App-Password": CLAVE }), consultas, CLAVE), true);
  igual(await autorizado(pedir({ "X-App-Password": "otra" }), consultas, CLAVE), false);
  igual(await autorizado(pedir({ Authorization: "Bearer t-marketing", "X-App-Password": CLAVE }), consultas, CLAVE), true, "sin permiso por sesión pero con la clave");
});

Deno.test("si falla la validación de la sesión no se abre la puerta", async () => {
  const rotas: Consultas = { usuario: () => Promise.reject(new Error("caído")), rol: () => Promise.resolve("dueno") };
  const original = console.error; console.error = () => {};
  igual(await autorizado(pedir({ Authorization: "Bearer t-dueno" }), rotas, CLAVE), false);
  console.error = original;
});
