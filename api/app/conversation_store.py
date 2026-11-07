"""PostgreSQL persistence for LangGraph checkpoints and user-visible conversations."""

import json
import os
import secrets
from contextlib import contextmanager
from typing import Any, Iterator
from uuid import UUID
from urllib.parse import quote_plus

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    database_user = quote_plus(os.getenv("POSTGRES_USER", "frodo"))
    database_password = quote_plus(os.getenv("POSTGRES_PASSWORD", ""))
    database_host = os.getenv("POSTGRES_HOST", "sql-headless")
    database_port = os.getenv("POSTGRES_PORT", "5432")
    database_name = quote_plus(os.getenv("POSTGRES_DB", "frodo"))
    DATABASE_URL = (
        f"postgresql://{database_user}:{database_password}@"
        f"{database_host}:{database_port}/{database_name}"
    )

db_pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=1,
    max_size=int(os.getenv("DATABASE_POOL_SIZE", "10")),
    kwargs={"autocommit": True, "prepare_threshold": 0},
    open=False,
)
checkpointer = PostgresSaver(db_pool)
memory_store = PostgresStore(db_pool)
_jwt_signing_key: str | None = None


def _load_jwt_signing_key(connection: Any) -> str:
    """Create one shared signing key, preserving an existing deployment on upgrade."""
    row = connection.execute(
        "SELECT value FROM app_secrets WHERE name = %s", ("jwt_signing_key",)
    ).fetchone()
    if row is None:
        # The old environment setting is only a one-time migration source.
        # After initialization the database is authoritative for every replica.
        candidate = os.getenv("JWT_SECRET") or secrets.token_hex(32)
        if len(candidate) < 32:
            raise RuntimeError("JWT_SECRET must contain at least 32 characters for migration.")
        connection.execute(
            """
            INSERT INTO app_secrets (name, value)
            VALUES (%s, %s)
            ON CONFLICT (name) DO NOTHING
            """,
            ("jwt_signing_key", candidate),
        )
        # Read the persisted winner rather than a locally generated candidate.
        row = connection.execute(
            "SELECT value FROM app_secrets WHERE name = %s", ("jwt_signing_key",)
        ).fetchone()
    if row is None or not isinstance(row[0], str) or len(row[0]) < 32:
        raise RuntimeError("The stored JWT signing key is missing or invalid.")
    return row[0]


def get_jwt_signing_key() -> str:
    """Return the shared key only after durable storage has initialized."""
    if _jwt_signing_key is None:
        raise RuntimeError("JWT signing key is unavailable before store initialization.")
    return _jwt_signing_key


def open_store() -> None:
    """Open the shared pool and create checkpoint/application tables."""
    global _jwt_signing_key
    _jwt_signing_key = None
    db_pool.open(wait=True)
    with db_pool.connection() as connection:
        # Both API replicas may start together. Serialize schema migrations so
        # PostgresSaver.setup() cannot race on its migration table.
        connection.execute("SELECT pg_advisory_lock(728194205)")
        try:
            checkpointer.setup()
            memory_store.setup()
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id UUID PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            connection.execute(
                "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id BIGSERIAL PRIMARY KEY,
                    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    model TEXT,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS conversation_messages_conversation_created_idx
                ON conversation_messages (conversation_id, created_at, id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS conversations_updated_idx
                ON conversations (updated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS conversations_user_updated_idx
                ON conversations (user_id, updated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_secrets (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            signing_key = _load_jwt_signing_key(connection)
        finally:
            connection.execute("SELECT pg_advisory_unlock(728194205)")
    _jwt_signing_key = signing_key


def close_store() -> None:
    """Close database connections during application shutdown."""
    global _jwt_signing_key
    _jwt_signing_key = None
    db_pool.close()


def ping_store() -> bool:
    """Return whether PostgreSQL is reachable."""
    with db_pool.connection() as connection:
        return connection.execute("SELECT 1").fetchone() == (1,)


def latest_checkpoint_id(thread_id: str) -> str | None:
    """Return the newest LangGraph checkpoint identifier for one thread."""
    checkpoint = checkpointer.get_tuple(
        {"configurable": {"thread_id": thread_id}}
    )
    if checkpoint is None:
        return None
    identifier = checkpoint.config.get("configurable", {}).get("checkpoint_id")
    return str(identifier) if identifier else None


@contextmanager
def _dict_cursor() -> Iterator[Any]:
    with db_pool.connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            yield cursor


def ensure_conversation(conversation_id: str, user_id: str, question: str) -> None:
    """Create a conversation on its first question without overwriting its title."""
    title = question.strip().replace("\n", " ")[:80] or "새 대화"
    with db_pool.connection() as connection:
        connection.execute(
            """
            INSERT INTO conversations (id, user_id, title)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (UUID(conversation_id), UUID(user_id), title),
        )
        owner = connection.execute(
            "SELECT user_id FROM conversations WHERE id = %s", (UUID(conversation_id),)
        ).fetchone()
        if owner is None or owner[0] != UUID(user_id):
            raise PermissionError("Conversation belongs to another user.")


def append_turn(
    conversation_id: str,
    question: str,
    answer: str,
    model: str,
    metadata: dict[str, Any],
) -> None:
    """Persist one completed user/assistant turn atomically."""
    identifier = UUID(conversation_id)
    with db_pool.connection() as connection:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO conversation_messages
                    (conversation_id, role, content, model, metadata)
                VALUES
                    (%s, 'user', %s, %s, '{}'::jsonb),
                    (%s, 'assistant', %s, %s, %s::jsonb)
                """,
                (
                    identifier,
                    question,
                    model,
                    identifier,
                    answer,
                    model,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                (identifier,),
            )


def list_conversations(user_id: str) -> list[dict[str, Any]]:
    """List conversations newest first with their latest message preview."""
    with _dict_cursor() as cursor:
        cursor.execute(
            """
            SELECT c.id::text, c.title, c.created_at, c.updated_at,
                   COUNT(m.id)::int AS message_count,
                   LEFT(COALESCE((
                       SELECT content FROM conversation_messages latest
                       WHERE latest.conversation_id = c.id
                       ORDER BY latest.created_at DESC, latest.id DESC LIMIT 1
                   ), ''), 160) AS preview
            FROM conversations c
            LEFT JOIN conversation_messages m ON m.conversation_id = c.id
            WHERE c.user_id = %s
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            """,
            (UUID(user_id),),
        )
        return list(cursor.fetchall())


def get_conversation(conversation_id: str, user_id: str) -> dict[str, Any] | None:
    """Load one conversation and all durable user-visible messages."""
    identifier = UUID(conversation_id)
    with _dict_cursor() as cursor:
        cursor.execute(
            """
            SELECT id::text, title, created_at, updated_at
            FROM conversations WHERE id = %s AND user_id = %s
            """,
            (identifier, UUID(user_id)),
        )
        conversation = cursor.fetchone()
        if conversation is None:
            return None
        cursor.execute(
            """
            SELECT id, role, content, model, metadata, created_at
            FROM conversation_messages
            WHERE conversation_id = %s
            ORDER BY created_at, id
            """,
            (identifier,),
        )
        conversation["messages"] = list(cursor.fetchall())
        return conversation


def delete_conversation(conversation_id: str, user_id: str) -> bool:
    """Delete the user-visible conversation and all LangGraph checkpoints."""
    identifier = UUID(conversation_id)
    with db_pool.connection() as connection:
        with connection.transaction():
            deleted = connection.execute(
                "DELETE FROM conversations WHERE id = %s AND user_id = %s RETURNING id",
                (identifier, UUID(user_id)),
            ).fetchone()
            if deleted is None:
                return False
            checkpointer.delete_thread(conversation_id)
    return True
