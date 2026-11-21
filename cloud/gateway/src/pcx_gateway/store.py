"""Portable startup migration, hashed credentials, reported state and durable audit/log."""

import hashlib
import json
import time
from dataclasses import dataclass

import msgspec
from pcx_contracts.world import WorldState
from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import create_async_engine

from .config import Settings
from .reported import apply_reported

metadata = MetaData()
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
    Column("world", Text),
    Column("snapshot_cursor", BigInteger),
    Column("reported_cursor", BigInteger),
    Column("last_seen", Float),
)
tokens = Table(
    "tokens",
    metadata,
    Column("hash", String, primary_key=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("space_id", ForeignKey("spaces.id"), nullable=False),
)
events = Table(
    "events",
    metadata,
    Column("node_id", ForeignKey("nodes.id"), primary_key=True),
    Column("seq", BigInteger, primary_key=True),
    Column("type", String, nullable=False),
    Column("payload", Text, nullable=False),
)
tool_calls = Table(
    "tool_calls",
    metadata,
    Column("id", String, primary_key=True),
    Column("node_id", ForeignKey("nodes.id"), nullable=False),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("tool", String, nullable=False),
    Column("args", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("finished_at", Float),
    Column("ok", Integer),
)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Identity:
    user_id: str
    space_id: str
    node_id: str


class Store:
    def __init__(self, url: str):
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+asyncpg://" + url[len(prefix) :]
        if url.startswith("sqlite://"):
            url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        self.engine = create_async_engine(url)

    async def start(self, settings: Settings):
        async with self.engine.begin() as db:
            await db.run_sync(metadata.create_all)
            # Outstanding actions from a previous process must never be silently replayed.
            await db.execute(
                update(tool_calls)
                .where(tool_calls.c.status == "pending")
                .values(status="interrupted", finished_at=time.time())
            )
        if bool(settings.user_token) != bool(settings.node_token):
            raise ValueError("Set both PCX_USER_TOKEN and PCX_NODE_TOKEN")
        if settings.user_token and settings.node_token:
            await self.provision(settings)

    async def provision(self, s: Settings):
        """Explicit env provisioning; existing ownership cannot be reassigned accidentally."""
        async with self.engine.begin() as db:
            for table, key, values in (
                (users, users.c.id == s.user_id, {"id": s.user_id}),
                (spaces, spaces.c.id == s.space_id, {"id": s.space_id, "user_id": s.user_id}),
                (
                    nodes,
                    nodes.c.id == s.node_id,
                    {"id": s.node_id, "space_id": s.space_id, "token_hash": digest(s.node_token)},
                ),
                (
                    tokens,
                    tokens.c.hash == digest(s.user_token),
                    {"hash": digest(s.user_token), "user_id": s.user_id, "space_id": s.space_id},
                ),
            ):
                existing = (await db.execute(select(table).where(key))).mappings().first()
                if existing is None:
                    await db.execute(insert(table).values(**values))
                elif any(existing[k] != v for k, v in values.items()):
                    raise ValueError(
                        f"Provisioning conflicts with existing {table.name}; rotate explicitly in DB"
                    )

    async def identity(self, token: str) -> Identity | None:
        async with self.engine.connect() as db:
            row = (
                await db.execute(
                    select(tokens.c.user_id, tokens.c.space_id, nodes.c.id)
                    .join(nodes, tokens.c.space_id == nodes.c.space_id)
                    .where(tokens.c.hash == digest(token))
                )
            ).first()
        return Identity(*row) if row else None

    async def authenticate_node(self, node_id: str, token: str) -> bool:
        async with self.engine.connect() as db:
            return (
                await db.execute(
                    select(nodes.c.id).where(and_(nodes.c.id == node_id, nodes.c.token_hash == digest(token)))
                )
            ).first() is not None

    async def node(self, node_id: str):
        async with self.engine.connect() as db:
            return (await db.execute(select(nodes).where(nodes.c.id == node_id))).mappings().one()

    async def cursor(self, node_id: str) -> int | None:
        async with self.engine.connect() as db:
            return await db.scalar(select(func.max(events.c.seq)).where(events.c.node_id == node_id))

    async def snapshot(self, node_id, snapshot):
        async with self.engine.begin() as db:
            previous = await db.scalar(select(nodes.c.reported_cursor).where(nodes.c.id == node_id))
            if snapshot.cursor < (previous or 0):
                raise ValueError("StateSnapshot atrasado: el cursor del nodo no puede reiniciarse")
            await db.execute(
                update(nodes)
                .where(nodes.c.id == node_id)
                .values(
                    world=msgspec.json.encode(snapshot.world).decode(),
                    snapshot_cursor=snapshot.cursor,
                    reported_cursor=snapshot.cursor,
                    last_seen=time.time(),
                )
            )

    async def seen(self, node_id):
        async with self.engine.begin() as db:
            await db.execute(update(nodes).where(nodes.c.id == node_id).values(last_seen=time.time()))

    async def append(self, node_id, batch):
        async with self.engine.begin() as db:
            node = (await db.execute(select(nodes).where(nodes.c.id == node_id))).mappings().one()
            world = msgspec.json.decode(node["world"], type=WorldState)
            reported_cursor = node["reported_cursor"] or 0
            # Replays after reconnect are idempotent; the node's sequence never resets.
            for event in batch.events:
                encoded = msgspec.json.encode(event).decode()
                exists = await db.scalar(
                    select(events.c.payload).where(
                        and_(events.c.node_id == node_id, events.c.seq == event.seq)
                    )
                )
                if exists is None:
                    await db.execute(
                        insert(events).values(
                            node_id=node_id,
                            seq=event.seq,
                            type=event.type,
                            payload=encoded,
                        )
                    )
                elif exists != encoded:
                    raise ValueError("Un seq existente no puede cambiar su contenido")
                if event.seq > reported_cursor:
                    apply_reported(world, event.payload, event.t_pub_ns)
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

    async def page(self, node_id, cursor, types, limit):
        async with self.engine.connect() as db:
            # Capture high-water BEFORE reading rows. A concurrent commit must not make us
            # return a cursor beyond an event missing from this page (Postgres READ COMMITTED).
            latest = await db.scalar(select(func.max(events.c.seq)).where(events.c.node_id == node_id))
            query = (
                select(events.c.seq, events.c.payload)
                .where(
                    and_(events.c.node_id == node_id, events.c.seq > cursor, events.c.seq <= (latest or 0))
                )
                .order_by(events.c.seq)
            )
            if types:
                query = query.where(events.c.type.in_(types))
            rows = (await db.execute(query.limit(limit + 1))).all()
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = rows[-1].seq if more else max(cursor, latest or 0)
        return [json.loads(row.payload) for row in rows], next_cursor, more

    async def audit(self, call_id, identity, tool, args, status="pending"):
        async with self.engine.begin() as db:
            await db.execute(
                insert(tool_calls).values(
                    id=call_id,
                    node_id=identity.node_id,
                    user_id=identity.user_id,
                    tool=tool,
                    args=json.dumps(args),
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

    async def close(self):
        await self.engine.dispose()
