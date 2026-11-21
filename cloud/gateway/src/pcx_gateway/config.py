import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite+aiosqlite:///./gateway.db"
    redis_url: str | None = None
    user_token: str | None = None
    node_token: str | None = None
    console_token: str | None = None
    """Token de personas (consola): único que puede aprobar confirmaciones de domótica. Nunca a un agente."""
    user_id: str = "demo-user"
    space_id: str = "demo-space"
    node_id: str = "demo-node"
    heartbeat_s: float = 10
    node_idle_s: float = 35
    retention_days: float = 30
    max_http_body: int = 1024 * 1024
    max_ws_frame: int = 8 * 1024 * 1024
    max_thumbnail: int = 512 * 1024
    max_pending_calls: int = 32

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            redis_url=os.getenv("REDIS_URL") or None,
            user_token=os.getenv("PCX_USER_TOKEN") or None,
            node_token=os.getenv("PCX_NODE_TOKEN") or None,
            console_token=os.getenv("PCX_CONSOLE_TOKEN") or None,
            user_id=os.getenv("PCX_USER_ID", cls.user_id),
            space_id=os.getenv("PCX_SPACE_ID", cls.space_id),
            node_id=os.getenv("PCX_NODE_ID", cls.node_id),
            retention_days=float(os.getenv("PCX_RETENTION_DAYS", cls.retention_days)),
        )
