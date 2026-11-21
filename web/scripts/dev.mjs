// `pnpm dev -p 9300` como en Next: traduce -p/--port a `astro dev --port`.
import { spawn } from "node:child_process";

const args = process.argv.slice(2);
const out = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === "-p" || args[i] === "--port") out.push("--port", args[++i]);
  else out.push(args[i]);
}
if (!out.includes("--port")) out.push("--port", "9300");

const child = spawn("astro", ["dev", ...out], { stdio: "inherit", shell: process.platform === "win32" });
child.on("exit", (code) => process.exit(code ?? 0));
