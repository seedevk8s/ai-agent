"""User-scoped long-term memories backed by LangGraph PostgresStore."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from .conversation_store import memory_store
from .graph import get_llm


def recall_memories(user_id: str, query: str, limit: int = 8) -> list[str]:
    # This deployment uses stock PostgreSQL without pgvector, so Store retrieval
    # is namespace-scoped and does not require a semantic-vector index.
    items = memory_store.search(("memories", user_id), limit=limit)
    return [str(item.value.get("content", "")).strip() for item in items if item.value.get("content")]


def remember_turn(user_id: str, question: str, answer: str, model: str) -> list[str]:
    """Extract only durable user facts; do not store transient questions or secrets."""
    response = get_llm(model).invoke(
        [
            SystemMessage(content=(
                "Extract durable, future-useful facts explicitly stated by the user, such as "
                "preferences, identity, ongoing projects, or standing goals. Never store passwords, "
                "tokens, financial data, health data, or guesses. Return a JSON array of short Korean "
                "sentences. Return [] when nothing should be remembered."
            )),
            HumanMessage(content=f"사용자 메시지:\n{question}\n\n도우미 답변(맥락용):\n{answer[:2000]}"),
        ]
    )
    raw = response.content if isinstance(response.content, str) else "[]"
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        memories = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(memories, list):
        return []
    existing = set(recall_memories(user_id, question, limit=100))
    saved: list[str] = []
    for memory in memories[:5]:
        content = str(memory).strip()[:500]
        if not content or content in existing:
            continue
        memory_store.put(
            ("memories", user_id),
            str(uuid4()),
            {"content": content, "created_at": datetime.now(timezone.utc).isoformat()},
        )
        saved.append(content)
    return saved
