"""Cola de pedidos de features: `requests/NNN-slug.md`, uno por pedido.

La secretaria (h4) escribe el archivo; la unidad que programa en vivo (h3) lo toma, lo implementa
con hot reload y lo cierra. Formato (frontmatter simple + cuerpo):

    ---
    id: 003
    title: halo que siga al cocodrilo
    kind: visual            # visual | sound | tool | fix
    from: secretaria        # quién lo pidió (secretaria, Max, h4…)
    status: open            # open | doing | done | rejected
    created: 2026-10-15T18:50
    ---
    Max quiere que el halo del cocodrilo cambie de color cuando lo levanta.

El MCP del playground lee y crea estos archivos; nadie los edita a mano salvo el estado.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

KINDS = ("visual", "sound", "tool", "fix")
STATUSES = ("open", "doing", "done", "rejected")


@dataclass
class Request:
    id: str
    title: str
    kind: str
    sender: str
    status: str
    created: str
    body: str
    path: str

    def to_json(self) -> dict:
        return asdict(self)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "pedido"


class RequestQueue:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _parse(self, path: Path) -> Request | None:
        text = path.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
        if not m:
            return None
        meta = {}
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.split("#")[0].strip() if k.strip() in ("kind", "status") else v.strip()
        return Request(meta.get("id", path.stem[:3]), meta.get("title", path.stem), meta.get("kind", "visual"),
                       meta.get("from", "?"), meta.get("status", "open"), meta.get("created", ""),
                       m.group(2).strip(), str(path))

    def list(self, status: str | None = None) -> list[Request]:
        out = []
        for p in sorted(self.folder.glob("[0-9][0-9][0-9]-*.md")):
            r = self._parse(p)
            if r and (status is None or r.status == status):
                out.append(r)
        return out

    def next_id(self) -> str:
        ids = [int(p.name[:3]) for p in self.folder.glob("[0-9][0-9][0-9]-*.md")]
        return f"{(max(ids) + 1) if ids else 1:03d}"

    def add(self, title: str, body: str, kind: str = "visual", sender: str = "secretaria") -> Request:
        if kind not in KINDS:
            raise ValueError(f"kind debe ser uno de {KINDS}")
        rid = self.next_id()
        created = time.strftime("%Y-%m-%dT%H:%M")
        path = self.folder / f"{rid}-{_slug(title)}.md"
        path.write_text(f"---\nid: {rid}\ntitle: {title}\nkind: {kind}\nfrom: {sender}\nstatus: open\n"
                        f"created: {created}\n---\n{body.strip()}\n", encoding="utf-8")
        return self._parse(path)

    def set_status(self, rid: str, status: str, note: str = "") -> Request:
        if status not in STATUSES:
            raise ValueError(f"status debe ser uno de {STATUSES}")
        for p in self.folder.glob(f"{rid}-*.md"):
            text = p.read_text(encoding="utf-8")
            text = re.sub(r"^status: .*$", f"status: {status}", text, count=1, flags=re.M)
            if note:
                text = text.rstrip("\n") + f"\n\n> {time.strftime('%Y-%m-%d %H:%M')} · {status}: {note}\n"
            p.write_text(text, encoding="utf-8")
            return self._parse(p)
        raise KeyError(rid)

    def summary(self) -> dict:
        reqs = self.list()
        return {s: sum(1 for r in reqs if r.status == s) for s in STATUSES} | {"total": len(reqs)}
