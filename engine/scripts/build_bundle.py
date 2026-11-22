"""Exportación local revisada: git archive + snapshot explícito, sin publicar."""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import io
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import zipfile

BASE = "8e5a1be24e4c773183185c300812783ffd151bd9"
OVERLAY = ("playground/dale.py", "playground/recalib.py", "playground/scene.py",
           "slscan/capture.py", "playground/radiometria.py")
IMAGES = ("docs/imagenes/cuadro-colores.png", "docs/imagenes/tela-y-texturas.png")
LIMIT = 50_000_000
ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".js", ".mjs", ".css", ".html", ".ps1"}
PRIVATE_IP = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}|"
    r"\bgithub_pat_[A-Za-z0-9_]{30,}|\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}|"
    r"\bxox[baprs]-[A-Za-z0-9-]{20,}"
)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def sanitize(text):
    # Referencias a código de clientes se retiran de la copia compartible.
    text = re.sub(r"\*\*Nota\*\*: Max mencionó el código de \x53mart Vision.*?decida otra cosa\.",
                  "**Nota**: código de un proyecto de cliente, fuera de alcance. Se trabaja con librerías abiertas.",
                  text, flags=re.DOTALL)
    text = re.sub(r"- \*\*\x53mart Vision\*\*: fuera hasta que Max lo decida con h1\.",
                  "- **Código de cliente**: fuera de alcance.", text)
    text = re.sub(r"(?i)\x41B\s+InBev|\x53mart\s+Vision", "proyecto de cliente, fuera de alcance", text)
    # Rutas/usuarios del montaje original; los marcadores mantienen la sintaxis.
    text = re.sub(r"/Users/[^/\s\"'`<>]+", "/Users/USUARIO", text)
    text = re.sub(r"/home/[^/\s\"'`<>]+", "/home/USUARIO", text)
    text = re.sub(r"(?i)\x52oberto(?: \x4dardones)?", "USUARIO_PC", text)
    text = re.sub(r"(?i)\x4eEW-WHITE-4K|\x4daxs-MacBook-Air", "PC-EJEMPLO", text)
    text = text.replace('cuenta `max`', 'cuenta `USUARIO`')
    text = re.sub(r"(?i)C:(\\{1,2})Users\1[^\\\s\"'`<>]+", r"C:\1Users\1USUARIO", text)

    def address(m):
        address = ipaddress.ip_address(m[0])
        # Rangos de firewall son parte del código; no son destinos personales.
        if text[m.end():m.end()+1] == "/" or m[0] in {"0.0.0.0", "255.255.255.255"} or address.is_loopback:
            return m[0]
        if address.is_private or address in ipaddress.ip_network("100.64.0.0/10"):
            return "192.0.2.10"
        return m[0]

    text = PRIVATE_IP.sub(address, text)
    text = re.sub(r"(?m)ssh-(?:ed25519|rsa) AAA[A-Za-z0-9+/=]{35,}(?: [^\r\n\"'`<]+)?",
                  "ssh-ed25519 TU_LLAVE_PUBLICA", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.(?:cl|com|net|org)\b", "usuario@example.invalid", text)
    # IDs de voces del montaje original; configurar con la cuenta receptora.
    text = re.sub(r'("voz_id"\s*:\s*)"[A-Za-z0-9]{20}"', r'\1"TU_VOZ_ID"', text)
    text = re.sub(r'("voces_respaldo"\s*:\s*)\["[A-Za-z0-9]{20}"\]', r'\1[]', text)
    return text


def excluded(name):
    p = Path(name)
    if p.parts[0] in {"reports", "captures", "sltk", ".git", ".claude", ".venv", ".pytest_cache"}:
        return True
    if name.startswith("windows/mail-"):
        return True
    return any(x.startswith(".env") or x in {"__pycache__", ".DS_Store"} for x in p.parts) or p.suffix in {".pem", ".key", ".p12", ".log", ".pyc"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise SystemExit("La salida ya existe; usa otro nombre para no sobrescribirla.")
    if git(a.source, "rev-parse", "HEAD").decode().strip() != BASE:
        raise SystemExit("Cambió la revisión fuente: revisar y actualizar BASE antes de exportar.")
    dirty = git(ROOT, "status", "--porcelain", "--untracked-files=no").decode().strip()
    if dirty:
        raise SystemExit("La rama de documentación tiene cambios sin commit.")
    archive = git(ROOT, "archive", "--format=zip", "HEAD")
    contents, omissions, replaced, overlays = {}, [], [], []
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        for entry in z.infolist():
            if entry.is_dir():
                continue
            name = entry.filename
            if excluded(name):
                omissions.append(sanitize(name))
                continue
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise SystemExit(f"Symlink no permitido: {name}")
            contents[name] = z.read(entry)
    for name in OVERLAY:
        f = a.source / name
        if f.is_symlink():
            raise SystemExit(f"Symlink no permitido: {name}")
        data = f.read_bytes()
        contents[name] = data
        overlays.append({"path": name, "source_sha256": hashlib.sha256(data).hexdigest(),
                         "origin": "working-tree snapshot, not committed by bundle"})
    for name, data in list(contents.items()):
        if name in IMAGES:
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise SystemExit(f"PNG inválido: {name}")
            continue
        if Path(name).suffix not in TEXT_SUFFIXES and name != ".gitignore":
            raise SystemExit(f"Tipo de archivo no revisado: {name}")
        text = data.decode("utf-8")
        # No mostrar valores ni hashes de credenciales detectadas.
        if SECRET.search(text):
            raise SystemExit(f"Posible secreto en {name}; revisar localmente antes de exportar.")
        # El exportador contiene expresiones regulares, no datos del equipo.
        clean = text if name == "scripts/build_bundle.py" else sanitize(text)
        if name == "playground/secretaria/config.py":
            clean = clean.replace('str(HOME / "bin" / "orq-creativo")', '"/usr/bin/false"')
        if clean != text:
            replaced.append(name)
        if Path(name).suffix == ".py":
            ast.parse(clean, filename=name)
        elif Path(name).suffix == ".json":
            json.loads(clean)
        contents[name] = clean.encode()
    # Comprobar que la fuente no cambió mientras se leía.
    for record in overlays:
        if hashlib.sha256((a.source / record["path"]).read_bytes()).hexdigest() != record["source_sha256"]:
            raise SystemExit("La fuente cambió durante la exportación; repetir el snapshot.")
    manifest = {
        "format": "sl-scanner-share-bundle/1", "created": dt.datetime.now(dt.timezone.utc).isoformat(),
        "base_revision": BASE, "docs_revision": git(ROOT, "rev-parse", "HEAD").decode().strip(),
        "working_tree_overlay": overlays, "excluded_tracked_files": omissions,
        "sanitized_files": replaced, "approved_images": list(IMAGES),
        "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                  for name, data in sorted(contents.items())},
        "excluded_untracked_categories": ["captures", "reports except two reviewed renders", "environments",
                                          "models", "sltk reference", "personal configuration and credentials"],
        "manifest_self_hash": "not included; checksum of complete ZIP delivered alongside",
    }
    contents["BUNDLE-MANIFEST.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode() + b"\n"
    total = sum(map(len, contents.values()))
    if total >= LIMIT:
        raise SystemExit("Contenido expandido supera 50 MB.")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(contents.items()):
            z.writestr("sl-scanner/" + name, data)
    data = out.getvalue()
    if len(data) >= LIMIT:
        raise SystemExit("ZIP supera 50 MB.")
    with a.output.open("xb") as f:
        f.write(data)
    sha = hashlib.sha256(data).hexdigest()
    a.output.with_suffix(a.output.suffix + ".sha256").write_text(sha + "  " + a.output.name + "\n")
    print(json.dumps({"zip": str(a.output), "bytes": len(data), "expanded_bytes": total,
                      "files": len(contents), "sha256": sha, "sanitized_files": len(replaced),
                      "excluded_tracked_files": len(omissions)}, indent=2))


if __name__ == "__main__":
    main()
