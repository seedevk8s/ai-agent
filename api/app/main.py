"""HTTP API for health checks, model selection, documents, and agent queries."""

import os
import socket
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage
from psycopg import Error as PostgresError
from pydantic import BaseModel, Field
from typing import List
from .auth import CurrentUser, authenticate_user, create_access_token, create_user
from .conversation_store import (
    append_turn,
    close_store,
    delete_conversation,
    ensure_conversation,
    get_conversation,
    list_conversations,
    latest_checkpoint_id,
    open_store,
    ping_store,
)
from .graph import app_graph
from .ingest import delete_document, ingest_documents, ingest_file, list_documents
from .memory import recall_memories, remember_turn

API_VERSION = "1.1"  # Update manually for each API release.


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize durable PostgreSQL storage before serving requests."""
    open_store()
    try:
        yield
    finally:
        close_store()


def api_instance():
    """Return the hostname and IP of the API process serving this request."""
    hostname = os.getenv("HOSTNAME") or socket.gethostname()
    pod_ip = os.getenv("POD_IP")
    if not pod_ip:
        try:
            pod_ip = socket.gethostbyname(hostname)
        except OSError:
            pod_ip = "unknown"
    return {"hostname": hostname, "ip": pod_ip}

app = FastAPI(
    title="Tool-Calling Knowledge API (rag-app)",
    description="FastAPI service hosting a tool-calling knowledge agent with document and web search",
    version=API_VERSION,
    lifespan=lifespan,
)

# Allow the local web console in /front to call this API during development.
# Set FRONTEND_ORIGINS as a comma-separated list to add deployed front-end URLs.
frontend_origins = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost,http://127.0.0.1,http://localhost:3000,http://127.0.0.1:3000",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in frontend_origins if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


class RegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=60)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class QueryRequest(BaseModel):
    """Validated user question, conversation identifier, and selected model."""
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    model: str | None = None


class QueryResponse(BaseModel):
    """Agent answer with routing evidence, sources, and responding Pod identity."""
    conversation_id: str
    checkpoint_id: str | None = None
    model: str
    question: str
    generation: str
    documents: list[str]
    rewritten_queries: list[str]
    required_tools: list[str]
    routing_reason: str
    web_search_needed: bool
    answer_source: str
    api_hostname: str
    api_ip: str


class IngestRequest(BaseModel):
    """Plain-text documents submitted directly to the vector knowledge base."""
    documents: List[str]


MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
DEFAULT_LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5-mini")
ALLOWED_LLM_MODELS = tuple(
    dict.fromkeys(
        [
            DEFAULT_LLM_MODEL,
            *[
                model.strip()
                for model in os.getenv(
                    "ALLOWED_LLM_MODELS",
                    "gpt-5-mini,gpt-5.4-mini,gpt-5.6,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna",
                ).split(",")
                if model.strip()
            ],
        ]
    )
)


@app.get("/health", summary="Kubernetes Health Check")
def health_check():
    """
    Endpoint for K8s Liveness & Readiness Probes.
    """
    return {
        "status": "ok",
        "app": "rag-app",
        "version": API_VERSION,
        **api_instance(),
    }


@app.get("/models", summary="List allowed LLM models")
def list_models():
    """Expose the server-side model allowlist used by the Front model picker."""
    return {
        "default_model": DEFAULT_LLM_MODEL,
        "models": list(ALLOWED_LLM_MODELS),
    }


@app.get("/ready", summary="Kubernetes Readiness Check")
def readiness_check():
    """Report readiness only when shared PostgreSQL persistence is reachable."""
    try:
        if not ping_store():
            raise HTTPException(status_code=503, detail="PostgreSQL is not ready.")
    except PostgresError as exc:
        raise HTTPException(status_code=503, detail="PostgreSQL is not ready.") from exc
    return {"status": "ready", "app": "rag-app", "version": API_VERSION}


@app.post("/auth/register", response_model=AuthResponse, status_code=201)
def register(request: RegisterRequest):
    try:
        user = create_user(request.email, request.password, request.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AuthResponse(access_token=create_access_token(user["id"]), user=user)


@app.post("/auth/login", response_model=AuthResponse)
def login(request: LoginRequest):
    user = authenticate_user(request.email, request.password)
    if user is None:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    return AuthResponse(access_token=create_access_token(user["id"]), user=user)


@app.get("/auth/me")
def me(user: CurrentUser):
    return {"user": user}


def _validated_conversation_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="conversation_id must be a valid UUID."
        ) from exc


@app.get("/conversations", summary="List durable conversations")
def get_conversations(user: CurrentUser):
    """Return durable conversations in most-recently-used order."""
    try:
        return {"conversations": list_conversations(user["id"])}
    except PostgresError as exc:
        raise HTTPException(status_code=503, detail="Conversation store is unavailable.") from exc


@app.get("/conversations/{conversation_id}", summary="Get a durable conversation")
def get_conversation_detail(conversation_id: str, user: CurrentUser):
    """Return all user-visible messages for one conversation."""
    identifier = _validated_conversation_id(conversation_id)
    try:
        conversation = get_conversation(identifier, user["id"])
    except PostgresError as exc:
        raise HTTPException(status_code=503, detail="Conversation store is unavailable.") from exc
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    conversation["checkpoint_id"] = latest_checkpoint_id(identifier)
    return conversation


@app.delete("/conversations/{conversation_id}", summary="Delete a durable conversation")
def remove_conversation(conversation_id: str, user: CurrentUser):
    """Delete both user-visible messages and the matching LangGraph thread."""
    identifier = _validated_conversation_id(conversation_id)
    try:
        deleted = delete_conversation(identifier, user["id"])
    except PostgresError as exc:
        raise HTTPException(status_code=503, detail="Conversation store is unavailable.") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"status": "success", "conversation_id": identifier}


@app.post("/ingest", summary="Store & Embed Documents into Vector DB (Pod 2)")
def ingest(request: IngestRequest, _: CurrentUser):
    """
    Embeds provided text documents and stores them in Qdrant Vector DB (Pod 2).
    """
    if not request.documents:
        raise HTTPException(status_code=400, detail="Documents list cannot be empty.")
    
    result = ingest_documents(request.documents)
    return result


@app.post("/documents/upload", summary="Upload a file into the knowledge base")
async def upload_document(_: CurrentUser, file: UploadFile = File(...)):
    """Validate, parse, chunk, embed, and store one uploaded document in Qdrant."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="파일 이름이 필요합니다.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="파일은 최대 20MB까지 업로드할 수 있습니다.")
    try:
        # File parsing, embedding calls, and Qdrant writes are blocking work.
        # Keep them off the event loop so health probes remain responsive.
        return await run_in_threadpool(
            ingest_file,
            file.filename,
            content,
            file.content_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"문서 저장 실패: {exc}") from exc


@app.get("/documents", summary="List indexed documents")
def get_documents(_: CurrentUser):
    """Return document-level metadata aggregated from Qdrant chunk payloads."""
    try:
        return {"documents": list_documents()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"문서 목록 조회 실패: {exc}") from exc


@app.delete("/documents/{document_id}", summary="Delete an indexed document")
def remove_document(document_id: str, _: CurrentUser):
    """Delete every Qdrant chunk belonging to a document identifier."""
    try:
        delete_document(document_id)
        return {"status": "success", "document_id": document_id}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"문서 삭제 실패: {exc}") from exc


@app.post("/query", response_model=QueryResponse, summary="Execute Tool-Calling Agent Query")
def run_query(request: QueryRequest, user: CurrentUser):
    """
    Lets the model choose internal-document search, web search, both tools, or a direct answer.
    """
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    if request.conversation_id:
        conversation_id = _validated_conversation_id(request.conversation_id)
    else:
        conversation_id = str(uuid4())

    selected_model = request.model or DEFAULT_LLM_MODEL
    if selected_model not in ALLOWED_LLM_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model. Allowed models: {', '.join(ALLOWED_LLM_MODELS)}",
        )

    try:
        ensure_conversation(conversation_id, user["id"], question)
        memories = recall_memories(user["id"], question)
        inputs = {
            "question": question,
            "model": selected_model,
            "messages": [HumanMessage(content=question)],
            "tool_call_count": 0,
            "documents": [],
            "rewritten_queries": [],
            "required_tools": [],
            "executed_tools": [],
            "memories": memories,
        }
        output = app_graph.invoke(
            inputs,
            config={"configurable": {"thread_id": conversation_id}},
        )
        generation = output.get("generation", "No generation produced.")
        checkpoint_id = latest_checkpoint_id(conversation_id)
        append_turn(
            conversation_id,
            question,
            generation,
            selected_model,
            {
                "documents": output.get("documents", []),
                "rewritten_queries": output.get("rewritten_queries", []),
                "required_tools": output.get("required_tools", []),
                "routing_reason": output.get("routing_reason", "model discretion"),
                "answer_source": output.get("answer_source", "unknown"),
            },
        )
        try:
            remember_turn(user["id"], question, generation, selected_model)
        except Exception:
            pass
        instance = api_instance()

        return QueryResponse(
            conversation_id=conversation_id,
            checkpoint_id=checkpoint_id,
            model=selected_model,
            question=output.get("question", question),
            generation=generation,
            documents=output.get("documents", []),
            rewritten_queries=output.get("rewritten_queries", []),
            required_tools=output.get("required_tools", []),
            routing_reason=output.get("routing_reason", "model discretion"),
            web_search_needed=output.get("web_search_needed", False),
            answer_source=output.get("answer_source", "unknown"),
            api_hostname=instance["hostname"],
            api_ip=instance["ip"],
        )
    except PostgresError as exc:
        raise HTTPException(
            status_code=503, detail="Conversation store is temporarily unavailable."
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found.") from exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error executing RAG workflow: {str(e)}")


def start():
    """Start Uvicorn on the configured container port."""
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    start()
