// /install.sh de la landing: el mismo scripts/install/install.sh del repo, copiado al construir (sin duplicarlo).
import script from "../../../scripts/install/install.sh?raw";

export const GET = () => new Response(script, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
