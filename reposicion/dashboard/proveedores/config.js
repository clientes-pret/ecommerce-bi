// reposicion/dashboard/proveedores/config.js
// Único archivo a tocar: pegar la "anon public key" del proyecto Supabase de reposición
// (Supabase → Project Settings → API → anon public). Es una clave pública, NO la service_role.
// Lectura automática de remitos con IA. Dejar en false hasta cargar crédito en Anthropic y deployar
// la función (ver proveedores/README.md). Con false, Mati carga los ítems a mano.
export const LECTURA_IA = false;
export const SUPABASE_URL = "https://fvkbclkofsggbvwcxipa.supabase.co";
export const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZ2a2JjbGtvZnNnZ2J2d2N4aXBhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODYwNTc3NjcsImV4cCI6MjEwMTYzMzc2N30.UokwF-FeZKG1_zlOlIwlcWV0scw7xrHvGky5JSTz8PM";
