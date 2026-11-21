"""Persistencia: credenciales en hash, espejo reportado, log por stream, memoria del espacio y auditoría.

Una sola fuente durable (Postgres en Render, SQLite en local). `events.pos` es la posición global del gateway:
es el cursor de `events_wait` y sobrevive a `stream_reset` del nodo (el `seq` del nodo viaja en el sobre).
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass

import msgspec
from pcx_contracts.cloud import HistoryGap
from pcx_contracts.events import ObjectAppeared, ObjectMoved, ObjectRemoved
from pcx_contracts.world import WorldState
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    delete,
    func,
    insert,
    not_,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import create_async_engine

from .config import Settings
from .reported import apply_reported

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
GAP = "history_gap"
PosType = BigInteger().with_variant(Integer, "sqlite")  # SQLite solo autoincrementa INTEGER PRIMARY KEY.

metadata = MetaData()
schema_meta = Table(
    "schema_meta", metadata, Column("key", String, primary_key=True), Column("value", String, nullable=False)
)
users = Table("users", metadata, Column("id", String, primary_key=True))
spaces = Table(
    "spaces",
    metadata,
    Column("id", String, primary_key=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
)
nodes = Table(
    "nodes",
    metadata,
    Column("id", String, primary_key=True),
    Column("space_id", ForeignKey("spaces.id"), nullable=False, unique=True),
    Column("token_hash", String, nullable=False, unique=True),
    Column("stream_id", String),
    Column("world", Text),
    Column("snapshot_cursor", BigInteger),
    Column("reported_cursor", BigInteger),
    Column("history_complete", Boolean),
    Column("last_seen", Float),
)
tokens = Table(
    "tokens",
    metadata,
    Column("hash", String, primary_key=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("space_id", ForeignKey("spaces.id"), nullable=False),
    Column("scope", String, nullable=False),  # "mcp" (agentes) | "console" (personas: confirma domótica)
)
events = Table(
    "events",
    metadata,
    Column("pos", PosType, primary_key=True, autoincrement=True),
    Column("node_id", ForeignKey("nodes.id"), nullable=False),
    Column("space_id", ForeignKey("spaces.id"), nullable=False),
    Column("stream_id", String, nullable=False),
    Column("seq", BigInteger),  # NULL en filas de hueco (type = history_gap)
    Column("type", String, nullable=False),
    Column("t_pub_ns", BigInteger, nullable=False),
    Column("object_id", String),
    Column("label", String),
    Column("payload", Text, nullable=False),
    UniqueConstraint("node_id", "stream_id", "seq", name="uq_events_stream_seq"),
    Index("ix_events_node_pos", "node_id", "pos"),
    Index("ix_events_space_time", "space_id", "t_pub_ns"),
    Index("ix_events_space_type", "space_id", "type", "t_pub_ns"),
    Index("ix_events_space_object", "space_id", "object_id", "t_pub_ns"),
    sqlite_autoincrement=True,  # pos nunca se reutiliza, ni tras la retención
)
objects = Table(
    "objects",
    metadata,
    Column("space_id", ForeignKey("spaces.id"), primary_key=True),
    Column("object_id", String, primary_key=True),
    Column("label", String, nullable=False, default=""),
    Column("present", Boolean, nullable=False, default=False),
    Column("center_x", Float),
    Column("center_y", Float),
    Column("last_seen_ns", BigInteger),
    Column("thumb", LargeBinary),  # bytea en Postgres: solo el último recorte por objeto
    Column("thumb_mime", String),
    Column("thumb_at", Float),
    Index("ix_objects_space_label", "space_id", "label"),
)
confirmations = Table(
    "confirmations",
    metadata,
    Column("id", String, primary_key=True),
    Column("node_id", ForeignKey("nodes.id"), nullable=False),
    Column("space_id", ForeignKey("spaces.id"), nullable=False),
    Column("request_id", String, nullable=False),
    Column("action_digest", String, nullable=False),
    Column("summary", String, nullable=False, default=""),
    Column("status", String, nullable=False),  # pending | sent | approved | rejected | expired
    Column("via", String),
    Column("outcome", String),  # HomeCallDone.status
    Column("operator", String),
    Column("expires_ns", BigInteger),
    Column("created_at", Float, nullable=False),
    Column("resolved_at", Float),
    Index("ix_confirmations_space_status", "space_id", "status"),
)
tool_calls = Table(
    "tool_calls",
    metadata,
    Column("id", String, primary_key=True),
    Column("node_id", ForeignKey("nodes.id"), nullable=False),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("tool", String, nullable=False),
    Column("request_id", String),
    Column("args", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("finished_at", Float),
    Column("ok", Integer),
    Index("ix_tool_calls_node_created", "node_id", "created_at"),
)
MAX_AUDIT_ARGS = 16_384


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def like_escape(text_: str) -> str:
    return text_.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class Identity:
    user_id: str
    space_id: str
    node_id: str
    scope: str = "mcp"


class Store:
    def __init__(self, url: str):
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+asyncpg://" + url[len(prefix) :]
        if url.startswith("sqlite://"):
            url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        self.engine = create_async_engine(url, pool_pre_ping=True)

    async def start(self, settings: Settings):
        async with self.engine.begin() as db:
            await self._migrate(db)
            # Acciones de un proceso anterior nunca se reintentan solas.
            await db.execute(
                update(tool_calls)
                .where(tool_calls.c.status == "pending")
                .values(status="interrupted", finished_at=time.time())
            )
        if bool(settings.user_token) != bool(settings.node_token):
            raise ValueError("Define PCX_USER_TOKEN y PCX_NODE_TOKEN juntos")
        if settings.user_token and settings.node_token:
            await self.provision(settings)

    async def _migrate(self, db):
        """Migración versionada mínima: v2 crea todo; una versión desconocida detiene el arranque."""
        await db.run_sync(lambda conn: schema_meta.create(conn, checkfirst=True))
        current = await db.scalar(select(schema_meta.c.value).where(schema_meta.c.key == "version"))
        if current is None:
            await db.run_sync(metadata.create_all)
            await db.execute(insert(schema_meta).values(key="version", value=str(SCHEMA_VERSION)))
        elif int(current) != SCHEMA_VERSION:
            raise RuntimeError(f"Esquema DB v{current}; este gateway espera v{SCHEMA_VERSION}")

    async def provision(self, s: Settings):
        """Aprovisionamiento por env: el entorno manda sobre las credenciales, no sobre los dueños.

        Rotar = cambiar el env y redesplegar. El token de nodo se reemplaza, y los tokens de usuario y consola
        que ya no están en el env quedan revocados (sin PCX_CONSOLE_TOKEN no hay acceso de consola).
        Nunca reasigna un espacio ni un nodo a otro dueño.
        """
        if len({s.user_token, s.node_token, s.console_token} - {None}) != sum(
            bool(t) for t in (s.user_token, s.node_token, s.console_token)
        ):
            raise ValueError("Los tokens de usuario, nodo y consola deben ser distintos")
        node_hash = digest(s.node_token)
        wanted = {digest(t): scope for t, scope in ((s.user_token, "mcp"), (s.console_token, "console")) if t}
        async with self.engine.begin() as db:

            async def row(table, key):
                return (await db.execute(select(table).where(key))).mappings().first()

            if await row(users, users.c.id == s.user_id) is None:
                await db.execute(insert(users).values(id=s.user_id))
            space = await row(spaces, spaces.c.id == s.space_id)
            if space is None:
                await db.execute(insert(spaces).values(id=s.space_id, user_id=s.user_id))
            elif space["user_id"] != s.user_id:
                raise ValueError("El aprovisionamiento choca con spaces: el espacio tiene otro dueño")
            node = await row(nodes, nodes.c.id == s.node_id)
            if node is None:
                await db.execute(
                    insert(nodes).values(id=s.node_id, space_id=s.space_id, token_hash=node_hash)
                )
            elif node["space_id"] != s.space_id:
                raise ValueError("El aprovisionamiento choca con nodes: el nodo pertenece a otro espacio")
            elif node["token_hash"] != node_hash:
                await db.execute(update(nodes).where(nodes.c.id == s.node_id).values(token_hash=node_hash))
                log.warning("PCX_NODE_TOKEN rotado para %s: el token anterior queda revocado", s.node_id)
            # Revoca todo token de este usuario y espacio que no sea exactamente un (hash, scope) del env.
            stale = and_(tokens.c.user_id == s.user_id, tokens.c.space_id == s.space_id)
            if wanted:
                keep = [and_(tokens.c.hash == h, tokens.c.scope == scope) for h, scope in wanted.items()]
                stale = and_(stale, not_(or_(*keep)))
            revoked = (await db.execute(delete(tokens).where(stale))).rowcount
            if revoked:
                log.warning("Revocados %d tokens de usuario o consola que ya no están en el env", revoked)
            for h, scope in wanted.items():
                existing = await row(tokens, tokens.c.hash == h)
                if existing is None:
                    await db.execute(
                        insert(tokens).values(hash=h, user_id=s.user_id, space_id=s.space_id, scope=scope)
                    )
                elif (existing["user_id"], existing["space_id"]) != (s.user_id, s.space_id):
                    raise ValueError("El aprovisionamiento choca con tokens: el token pertenece a otro dueño")

    async def create_session(self) -> dict:
        """Sesión de la consola web: usuario, espacio y nodo nuevos con sus propios tokens (solo se guardan hashes)."""
        sid = secrets.token_hex(5)
        out = {
            "session_id": sid,
            "node_id": f"web-{sid}",
            "node_token": secrets.token_urlsafe(32),
            "user_token": secrets.token_urlsafe(32),
        }
        user_id, space_id = f"web-user-{sid}", f"web-space-{sid}"
        async with self.engine.begin() as db:
            await db.execute(insert(users).values(id=user_id))
            await db.execute(insert(spaces).values(id=space_id, user_id=user_id))
            await db.execute(
                insert(nodes).values(
                    id=out["node_id"], space_id=space_id, token_hash=digest(out["node_token"])
                )
            )
            await db.execute(
                insert(tokens).values(
                    hash=digest(out["user_token"]), user_id=user_id, space_id=space_id, scope="mcp"
                )
            )
        return out

    async def create_view_token(self, identity: "Identity") -> str:
        """Token de solo lectura para mirar un espacio en vivo (scope `view`): no sirve para MCP, /api ni /node."""
        token = secrets.token_urlsafe(24)
        async with self.engine.begin() as db:
            await db.execute(
                insert(tokens).values(
                    hash=digest(token), user_id=identity.user_id, space_id=identity.space_id, scope="view"
                )
            )
        return token

    async def identity(self, token: str) -> Identity | None:
        async with self.engine.connect() as db:
            row = (
                await db.execute(
                    select(tokens.c.user_id, tokens.c.space_id, nodes.c.id, tokens.c.scope)
                    .join(nodes, tokens.c.space_id == nodes.c.space_id)
                    .where(tokens.c.hash == digest(token))
                )
            ).first()
        return Identity(*row) if row else None

    async def authenticate_node(self, node_id: str, token: str) -> bool:
        async with self.engine.connect() as db:
            stored = await db.scalar(select(nodes.c.token_hash).where(nodes.c.id == node_id))
        return stored is not None and hmac.compare_digest(stored, digest(token))

    async def node_by_token(self, token: str):
        async with self.engine.connect() as db:
            return (
                (
                    await db.execute(
                        select(nodes.c.id, nodes.c.space_id).where(nodes.c.token_hash == digest(token))
                    )
                )
                .mappings()
                .first()
            )

    async def node(self, node_id: str):
        async with self.engine.connect() as db:
            return (await db.execute(select(nodes).where(nodes.c.id == node_id))).mappings().one()

    async def ping(self):
        async with self.engine.connect() as db:
            await db.execute(text("select 1"))

    async def resume_point(self, node_id: str) -> tuple[str | None, int | None]:
        """(`last_stream_id`, `last_cursor`) para `welcome`."""
        async with self.engine.connect() as db:
            stream_id = await db.scalar(select(nodes.c.stream_id).where(nodes.c.id == node_id))
            if stream_id is None:
                return None, None
            return stream_id, await self._last_seq(db, node_id, stream_id)

    @staticmethod
    async def _last_seq(db, node_id, stream_id):
        return await db.scalar(
            select(func.max(events.c.seq)).where(
                and_(events.c.node_id == node_id, events.c.stream_id == stream_id)
            )
        )

    async def cursor(self, node_id: str) -> int:
        """Posición global (cursor de `events_wait`)."""
        async with self.engine.connect() as db:
            return await db.scalar(select(func.max(events.c.pos)).where(events.c.node_id == node_id)) or 0

    async def snapshot(self, node_id, snapshot):
        async with self.engine.begin() as db:
            node = (await db.execute(select(nodes).where(nodes.c.id == node_id))).mappings().one()
            same_stream = node["stream_id"] == snapshot.stream_id
            if same_stream and snapshot.cursor < (node["reported_cursor"] or 0):
                raise ValueError("StateSnapshot atrasado: el cursor del stream no puede retroceder")
            await db.execute(
                update(nodes)
                .where(nodes.c.id == node_id)
                .values(
                    stream_id=snapshot.stream_id,
                    world=msgspec.json.encode(snapshot.world).decode(),
                    snapshot_cursor=snapshot.cursor,
                    reported_cursor=snapshot.cursor,
                    last_seen=time.time(),
                )
            )
            await self._sync_objects(db, node["space_id"], snapshot.world)

    @staticmethod
    async def _sync_objects(db, space_id, world: WorldState):
        present = {obj.id for obj in world.objects}
        await db.execute(
            update(objects)
            .where(and_(objects.c.space_id == space_id, objects.c.present.is_(True)))
            .where(objects.c.object_id.not_in(present) if present else text("1=1"))
            .values(present=False)
        )
        for obj in world.objects:
            await Store._upsert_object(
                db,
                space_id,
                obj.id,
                label=obj.label,
                present=True,
                center=obj.center_mm,
                seen=obj.last_seen_ns,
            )

    @staticmethod
    async def _upsert_object(db, space_id, object_id, *, label=None, present=None, center=None, seen=None):
        values = {}
        if label:
            values["label"] = label
        if present is not None:
            values["present"] = present
        if center is not None:
            values.update(center_x=center.x, center_y=center.y)
        if seen is not None:
            values["last_seen_ns"] = seen
        # Inserta-si-falta sin carrera (evento y subida de miniatura pueden llegar a la vez), luego actualiza.
        dialect_insert = pg_insert if db.dialect.name == "postgresql" else sqlite_insert
        await db.execute(
            dialect_insert(objects)
            .values(space_id=space_id, object_id=object_id, label="", present=False)
            .on_conflict_do_nothing(index_elements=["space_id", "object_id"])
        )
        if values:
            key = and_(objects.c.space_id == space_id, objects.c.object_id == object_id)
            await db.execute(update(objects).where(key).values(**values))

    async def seen(self, node_id):
        async with self.engine.begin() as db:
            await db.execute(update(nodes).where(nodes.c.id == node_id).values(last_seen=time.time()))

    async def set_history_complete(self, node_id, complete: bool):
        async with self.engine.begin() as db:
            await db.execute(update(nodes).where(nodes.c.id == node_id).values(history_complete=complete))

    async def gap(self, node_id, gap: HistoryGap):
        async with self.engine.begin() as db:
            space_id = await db.scalar(select(nodes.c.space_id).where(nodes.c.id == node_id))
            await db.execute(
                insert(events).values(
                    node_id=node_id,
                    space_id=space_id,
                    stream_id=gap.stream_id,
                    seq=None,
                    type=GAP,
                    t_pub_ns=time.time_ns(),
                    payload=msgspec.json.encode(gap).decode(),
                )
            )
            await db.execute(update(nodes).where(nodes.c.id == node_id).values(history_complete=False))

    async def append(self, node_id, batch):
        """Guarda un lote (idempotente por (stream, seq)) y aplica al espejo solo lo posterior al snapshot.
        Devuelve los eventos nuevos (para confirmaciones)."""
        fresh = []
        async with self.engine.begin() as db:
            node = (await db.execute(select(nodes).where(nodes.c.id == node_id))).mappings().one()
            if node["stream_id"] != batch.stream_id:
                raise ValueError("event_batch de un stream distinto al del último state_snapshot")
            space_id = node["space_id"]
            world = msgspec.json.decode(node["world"], type=WorldState)
            reported_cursor = node["reported_cursor"] or 0
            for event in batch.events:
                encoded = msgspec.json.encode(event).decode()
                key = and_(
                    events.c.node_id == node_id,
                    events.c.stream_id == batch.stream_id,
                    events.c.seq == event.seq,
                )
                exists = await db.scalar(select(events.c.payload).where(key))
                if exists is not None:
                    if exists != encoded:
                        raise ValueError("Un seq existente no puede cambiar su contenido")
                    continue
                payload = event.payload
                object_id = getattr(payload, "object_id", None) or getattr(
                    getattr(payload, "object", None), "id", None
                )
                label = getattr(getattr(payload, "object", None), "label", None) or getattr(
                    payload, "label", None
                )
                if object_id and not label:
                    label = await db.scalar(
                        select(objects.c.label).where(
                            and_(objects.c.space_id == space_id, objects.c.object_id == object_id)
                        )
                    )
                await db.execute(
                    insert(events).values(
                        node_id=node_id,
                        space_id=space_id,
                        stream_id=batch.stream_id,
                        seq=event.seq,
                        type=event.type,
                        t_pub_ns=event.t_pub_ns,
                        object_id=object_id,
                        label=label or None,
                        payload=encoded,
                    )
                )
                fresh.append(event)
                await self._remember(db, space_id, payload, event.t_pub_ns)
                if event.seq > reported_cursor:
                    apply_reported(world, payload, event.t_pub_ns)
                    reported_cursor = event.seq
            await db.execute(
                update(nodes)
                .where(nodes.c.id == node_id)
                .values(
                    world=msgspec.json.encode(world).decode(),
                    reported_cursor=reported_cursor,
                    last_seen=time.time(),
                )
            )
        return fresh

    async def _remember(self, db, space_id, payload, t_ns):
        if isinstance(payload, ObjectAppeared):
            o = payload.object
            await self._upsert_object(
                db, space_id, o.id, label=o.label, present=True, center=o.center_mm, seen=t_ns
            )
        elif isinstance(payload, ObjectMoved):
            await self._upsert_object(
                db, space_id, payload.object_id, present=True, center=payload.center_mm, seen=t_ns
            )
        elif isinstance(payload, ObjectRemoved):
            await self._upsert_object(
                db, space_id, payload.object_id, label=payload.label, present=False, seen=t_ns
            )

    async def page(self, node_id, cursor, types, limit):
        async with self.engine.connect() as db:
            # Alto de agua ANTES de leer filas: un commit concurrente no puede saltarse un evento.
            latest = await db.scalar(select(func.max(events.c.pos)).where(events.c.node_id == node_id)) or 0
            cursor = min(cursor, latest)  # Cursor de otra DB o inventado: se reubica en el final.
            query = (
                select(events.c.pos, events.c.type, events.c.payload)
                .where(and_(events.c.node_id == node_id, events.c.pos > cursor, events.c.pos <= latest))
                .order_by(events.c.pos)
            )
            if types:
                query = query.where(or_(events.c.type.in_(types), events.c.type == GAP))
            rows = (await db.execute(query.limit(limit + 1))).all()
            stream_id = await db.scalar(select(nodes.c.stream_id).where(nodes.c.id == node_id))
        more = len(rows) > limit
        rows = rows[:limit]
        gaps = [json.loads(r.payload) for r in rows if r.type == GAP]
        return {
            "stream_id": stream_id,
            "cursor": rows[-1].pos if more else latest,
            "events": [json.loads(r.payload) for r in rows if r.type != GAP],
            "gap": gaps[0] if gaps else None,
            "gaps": gaps,
            "has_more": more,
        }

    # ------------------------------------------------------------------ memoria
    async def history(self, space_id, *, query, since_ns, until_ns, types, limit):
        async with self.engine.connect() as db:
            matches = []
            object_ids = None
            if query:
                pattern = f"%{like_escape(query.lower())}%"
                rows = (
                    await db.execute(
                        select(objects)
                        .where(
                            and_(
                                objects.c.space_id == space_id,
                                or_(
                                    func.lower(objects.c.label).like(pattern, escape="\\"),
                                    func.lower(objects.c.object_id).like(pattern, escape="\\"),
                                ),
                            )
                        )
                        .order_by(objects.c.last_seen_ns.desc())
                        .limit(20)
                    )
                ).mappings()
                for r in rows:
                    matches.append(
                        {
                            "object_id": r["object_id"],
                            "label": r["label"],
                            "present": r["present"],
                            "center_mm": None if r["center_x"] is None else [r["center_x"], r["center_y"]],
                            "last_seen_ns": r["last_seen_ns"],
                            "has_thumbnail": r["thumb"] is not None,
                        }
                    )
                object_ids = [m["object_id"] for m in matches]
            q = select(events).where(and_(events.c.space_id == space_id, events.c.type != GAP))
            if since_ns is not None:
                q = q.where(events.c.t_pub_ns >= since_ns)
            if until_ns is not None:
                q = q.where(events.c.t_pub_ns <= until_ns)
            if types:
                q = q.where(events.c.type.in_(types))
            if object_ids is not None:
                q = q.where(events.c.object_id.in_(object_ids or [""]))
            rows = (await db.execute(q.order_by(events.c.t_pub_ns.desc()).limit(limit))).mappings()
            found = [
                {
                    "pos": r["pos"],
                    "type": r["type"],
                    "object_id": r["object_id"],
                    "label": r["label"],
                    "t_pub_ns": r["t_pub_ns"],
                    "payload": json.loads(r["payload"])["payload"],
                }
                for r in rows
            ]
        return {"matches": matches, "events": found}

    async def put_thumbnail(self, space_id, object_id, data: bytes, mime: str, label: str | None = None):
        async with self.engine.begin() as db:
            await self._upsert_object(db, space_id, object_id, label=label)
            await db.execute(
                update(objects)
                .where(and_(objects.c.space_id == space_id, objects.c.object_id == object_id))
                .values(thumb=data, thumb_mime=mime, thumb_at=time.time())
            )

    async def thumbnail(self, space_id, object_id):
        async with self.engine.connect() as db:
            return (
                await db.execute(
                    select(objects.c.thumb, objects.c.thumb_mime).where(
                        and_(objects.c.space_id == space_id, objects.c.object_id == object_id)
                    )
                )
            ).first()

    # ------------------------------------------------------------------ confirmaciones de domótica
    async def confirmation_upsert(self, node_id, space_id, **values):
        async with self.engine.begin() as db:
            cid = values["id"]
            existing = (
                (await db.execute(select(confirmations).where(confirmations.c.id == cid))).mappings().first()
            )
            if existing is None:
                await db.execute(
                    insert(confirmations).values(
                        node_id=node_id, space_id=space_id, status="pending", created_at=time.time(), **values
                    )
                )
            elif existing["node_id"] != node_id or existing["action_digest"] != values.get(
                "action_digest", existing["action_digest"]
            ):
                raise ValueError("confirmation_id reutilizado con otra acción u otro nodo")
            else:
                patch = {k: v for k, v in values.items() if v and k != "id"}
                await db.execute(update(confirmations).where(confirmations.c.id == cid).values(**patch))

    async def confirmation_update(self, node_id, cid, **values):
        async with self.engine.begin() as db:
            await db.execute(
                update(confirmations)
                .where(and_(confirmations.c.id == cid, confirmations.c.node_id == node_id))
                .values(**values)
            )

    async def confirmation(self, space_id, cid):
        async with self.engine.connect() as db:
            return (
                (
                    await db.execute(
                        select(confirmations).where(
                            and_(confirmations.c.id == cid, confirmations.c.space_id == space_id)
                        )
                    )
                )
                .mappings()
                .first()
            )

    async def confirmations(self, space_id, status=None):
        async with self.engine.connect() as db:
            q = select(confirmations).where(confirmations.c.space_id == space_id)
            if status:
                q = q.where(confirmations.c.status == status)
            rows = await db.execute(q.order_by(confirmations.c.created_at.desc()).limit(100))
            return [dict(r) for r in rows.mappings()]

    # ------------------------------------------------------------------ auditoría y retención
    async def audit(self, call_id, identity, tool, args, status="pending"):
        raw = json.dumps(args, ensure_ascii=False)
        if len(raw) > MAX_AUDIT_ARGS:
            raw = json.dumps({"truncated": True, "bytes": len(raw), "head": raw[:1024]})
        async with self.engine.begin() as db:
            await db.execute(
                insert(tool_calls).values(
                    id=call_id,
                    node_id=identity.node_id,
                    user_id=identity.user_id,
                    tool=tool,
                    request_id=args.get("request_id") if isinstance(args, dict) else None,
                    args=raw,
                    status=status,
                    created_at=time.time(),
                )
            )

    async def finish(self, call_id, status, ok=None):
        async with self.engine.begin() as db:
            await db.execute(
                update(tool_calls)
                .where(tool_calls.c.id == call_id)
                .values(status=status, ok=None if ok is None else int(ok), finished_at=time.time())
            )

    async def purge(self, retention_days: float):
        """Retención: borra eventos y auditoría más viejos; `objects` (última posición y miniatura) se conserva."""
        cutoff = time.time() - retention_days * 86400
        async with self.engine.begin() as db:
            await db.execute(delete(events).where(events.c.t_pub_ns < int(cutoff * 1e9)))
            await db.execute(delete(tool_calls).where(tool_calls.c.created_at < cutoff))
            await db.execute(delete(confirmations).where(confirmations.c.created_at < cutoff))

    async def close(self):
        await self.engine.dispose()
