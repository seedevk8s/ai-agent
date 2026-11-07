"""Document ingestion and Qdrant search operations."""

import io
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
from docx import Document
from langchain_openai import OpenAIEmbeddings
from pypdf import PdfReader

VECTOR_DB_URL = os.getenv("VECTOR_DB_URL", "http://vector-db-headless:6333")
COLLECTION_NAME = os.getenv("VECTOR_COLLECTION", "rag_documents")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
VECTOR_SIZE = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "180"))
RETRIEVAL_CANDIDATES = int(os.getenv("RETRIEVAL_CANDIDATES", "20"))
RETRIEVAL_FINAL_K = int(os.getenv("RETRIEVAL_FINAL_K", "5"))
RETRIEVAL_MIN_SCORE = float(os.getenv("RETRIEVAL_MIN_SCORE", "0.35"))
RETRIEVAL_DEDUP_THRESHOLD = float(os.getenv("RETRIEVAL_DEDUP_THRESHOLD", "0.9"))

logger = logging.getLogger(__name__)


def _embeddings() -> OpenAIEmbeddings:
    """Create the embedding client configured for Qdrant vector dimensions."""
    return OpenAIEmbeddings(model=EMBEDDING_MODEL)


def ensure_collection_exists() -> None:
    """Create the Qdrant collection on first use and validate it otherwise."""
    url = f"{VECTOR_DB_URL}/collections/{COLLECTION_NAME}"
    response = httpx.get(url, timeout=5.0)
    if response.status_code == 404:
        created = httpx.put(
            url,
            json={"vectors": {"size": VECTOR_SIZE, "distance": "Cosine"}},
            timeout=10.0,
        )
        created.raise_for_status()
    else:
        response.raise_for_status()


def split_text(text: str) -> list[str]:
    """Normalize text and split it into overlapping, boundary-aware chunks."""
    clean = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not clean:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(start + CHUNK_SIZE, len(clean))
        if end < len(clean):
            boundary = max(clean.rfind("\n", start, end), clean.rfind(". ", start, end))
            if boundary > start + CHUNK_SIZE // 2:
                end = boundary + 1
        chunks.append(clean[start:end].strip())
        if end >= len(clean):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [chunk for chunk in chunks if chunk]


def extract_file(filename: str, content: bytes) -> list[dict[str, Any]]:
    """Extract page-aware text from a supported uploaded file format."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        return [
            {"text": page.extract_text() or "", "page": number}
            for number, page in enumerate(reader.pages, start=1)
        ]
    if suffix == ".docx":
        document = Document(io.BytesIO(content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        return [{"text": text, "page": None}]
    if suffix in {".txt", ".md", ".csv"}:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("cp949", errors="replace")
        return [{"text": text, "page": None}]
    raise ValueError("지원하지 않는 파일 형식입니다. PDF, DOCX, TXT, MD, CSV만 업로드할 수 있습니다.")


def _upsert_chunks(chunks: Iterable[dict[str, Any]]) -> int:
    """Embed chunk text and atomically upsert vector points into Qdrant."""
    chunk_list = list(chunks)
    if not chunk_list:
        raise ValueError("저장할 텍스트가 없습니다.")
    ensure_collection_exists()
    vectors = _embeddings().embed_documents([chunk["text"] for chunk in chunk_list])
    points = [
        {"id": str(uuid.uuid4()), "vector": vector, "payload": chunk}
        for chunk, vector in zip(chunk_list, vectors)
    ]
    response = httpx.put(
        f"{VECTOR_DB_URL}/collections/{COLLECTION_NAME}/points?wait=true",
        json={"points": points},
        timeout=60.0,
    )
    response.raise_for_status()
    return len(points)


def ingest_file(filename: str, content: bytes, content_type: str | None = None) -> dict[str, Any]:
    """Convert one uploaded file into metadata-rich chunks and persist them."""
    document_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    sections = extract_file(filename, content)
    chunks: list[dict[str, Any]] = []
    index = 0
    for section in sections:
        for text in split_text(section["text"]):
            chunks.append({
                "text": text,
                "document_id": document_id,
                "filename": filename,
                "content_type": content_type or "application/octet-stream",
                "page": section["page"],
                "chunk_index": index,
                "created_at": created_at,
                "file_size": len(content),
            })
            index += 1
    stored_count = _upsert_chunks(chunks)
    return {
        "status": "success",
        "document_id": document_id,
        "filename": filename,
        "stored_count": stored_count,
        "file_size": len(content),
        "created_at": created_at,
    }


def ingest_documents(docs: list[str]) -> dict[str, Any]:
    """Store directly submitted plain-text documents as a single document set."""
    document_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    chunks = []
    index = 0
    for doc in docs:
        for text in split_text(doc):
            chunks.append({
                "text": text,
                "document_id": document_id,
                "filename": "직접 입력 문서",
                "content_type": "text/plain",
                "page": None,
                "chunk_index": index,
                "created_at": created_at,
                "file_size": len(doc.encode("utf-8")),
            })
            index += 1
    stored_count = _upsert_chunks(chunks)
    return {"status": "success", "document_id": document_id, "stored_count": stored_count}


def _tokenize(value: str) -> set[str]:
    """Return normalized Korean/Latin/numeric tokens for lightweight reranking."""
    return set(re.findall(r"[0-9A-Za-z가-힣]+", value.lower()))


def _token_overlap(query: str, text: str) -> float:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0
    return len(query_tokens & _tokenize(text)) / len(query_tokens)


def _is_near_duplicate(text: str, selected: list[dict[str, Any]]) -> bool:
    tokens = _tokenize(text)
    if not tokens:
        return any(not _tokenize(item.get("text", "")) for item in selected)
    for item in selected:
        other = _tokenize(item.get("text", ""))
        union = tokens | other
        if union and len(tokens & other) / len(union) >= RETRIEVAL_DEDUP_THRESHOLD:
            return True
    return False


def rerank_documents(
    query: str, candidates: list[dict[str, Any]], limit: int = RETRIEVAL_FINAL_K
) -> list[dict[str, Any]]:
    """Deduplicate and rerank candidates using semantic and lexical relevance."""
    unique: list[dict[str, Any]] = []
    for candidate in sorted(
        candidates, key=lambda item: float(item.get("score", 0)), reverse=True
    ):
        if not _is_near_duplicate(candidate.get("text", ""), unique):
            unique.append(candidate)

    reranked: list[dict[str, Any]] = []
    for candidate in unique:
        vector_score = max(0.0, min(1.0, float(candidate.get("score", 0))))
        lexical_score = _token_overlap(query, candidate.get("text", ""))
        rerank_score = 0.75 * vector_score + 0.25 * lexical_score
        reranked.append(
            {
                **candidate,
                "vector_score": vector_score,
                "lexical_score": lexical_score,
                "rerank_score": rerank_score,
            }
        )
    reranked.sort(key=lambda item: item["rerank_score"], reverse=True)
    return reranked[:limit]


def search_document_candidates(
    query: str, limit: int = RETRIEVAL_CANDIDATES
) -> list[dict[str, Any]]:
    """Embed a query and return raw Qdrant candidates for later reranking."""
    ensure_collection_exists()
    vector = _embeddings().embed_query(query)
    response = httpx.post(
        f"{VECTOR_DB_URL}/collections/{COLLECTION_NAME}/points/query",
        json={"query": vector, "limit": limit, "with_payload": True},
        timeout=30.0,
    )
    response.raise_for_status()
    points = response.json().get("result", {}).get("points", [])
    return [
        {**point.get("payload", {}), "score": point.get("score", 0)}
        for point in points
    ]


def search_documents(query: str, limit: int = RETRIEVAL_FINAL_K) -> list[dict[str, Any]]:
    """Retrieve a broad candidate set, deduplicate it, and return reranked chunks."""
    candidates = search_document_candidates(query, RETRIEVAL_CANDIDATES)
    results = rerank_documents(query, candidates, limit)
    logger.info(
        "rag_retrieval query_length=%d candidates=%d final=%d top_vector=%.3f top_rerank=%.3f",
        len(query),
        len(candidates),
        len(results),
        max((float(item.get("score", 0)) for item in candidates), default=0.0),
        float(results[0].get("rerank_score", 0)) if results else 0.0,
    )
    return results


def list_documents() -> list[dict[str, Any]]:
    """Aggregate Qdrant chunk payloads into document-level metadata records."""
    ensure_collection_exists()
    response = httpx.post(
        f"{VECTOR_DB_URL}/collections/{COLLECTION_NAME}/points/scroll",
        json={"limit": 1000, "with_payload": True, "with_vector": False},
        timeout=20.0,
    )
    response.raise_for_status()
    points = response.json().get("result", {}).get("points", [])
    grouped: dict[str, dict[str, Any]] = {}
    for point in points:
        payload = point.get("payload", {})
        doc_id = payload.get("document_id")
        if not doc_id:
            continue
        if doc_id not in grouped:
            grouped[doc_id] = {
                "document_id": doc_id,
                "filename": payload.get("filename", "이름 없는 문서"),
                "content_type": payload.get("content_type"),
                "file_size": payload.get("file_size", 0),
                "created_at": payload.get("created_at"),
                "chunk_count": 0,
            }
        grouped[doc_id]["chunk_count"] += 1
    return sorted(grouped.values(), key=lambda item: item.get("created_at") or "", reverse=True)


def delete_document(document_id: str) -> None:
    """Delete all Qdrant points whose payload matches the document identifier."""
    response = httpx.post(
        f"{VECTOR_DB_URL}/collections/{COLLECTION_NAME}/points/delete?wait=true",
        json={"filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]}},
        timeout=20.0,
    )
    response.raise_for_status()
