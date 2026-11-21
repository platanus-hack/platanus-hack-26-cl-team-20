import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite+aiosqlite:///./gateway.db"
    redis_url: str | None = None
    user_token: str | None = None
    node_token: str | None = None
    user_id: str = "demo-user"
    space_id: str = "demo-space"
    node_id: str = "demo-node"
    heartbeat_s: float = 10
    node_idle_s: float = 35

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            redis_url=os.getenv("REDIS_URL"),
            user_token=os.getenv("PCX_USER_TOKEN"),
            node_token=os.getenv("PCX_NODE_TOKEN"),
            user_id=os.getenv("PCX_USER_ID", "demo-user"),
            space_id=os.getenv("PCX_SPACE_ID", "demo-space"),
            node_id=os.getenv("PCX_NODE_ID", "demo-node"),
        )
