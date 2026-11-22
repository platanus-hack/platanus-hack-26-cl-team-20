// /install.ps1 de la landing: el mismo scripts/install/install.ps1 del repo, copiado al construir (sin duplicarlo).
import script from "../../../scripts/install/install.ps1?raw";

export const GET = () => new Response(script, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
