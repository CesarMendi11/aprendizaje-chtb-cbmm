from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select, text

from src.config.neo4j_settings import Neo4jSettings
from src.config.ollama_settings import OllamaEmbeddingSettings
from src.database.enums import KnowledgeVersionStatus
from src.database.models import (
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    SyncJob,
)
from src.graph.client import Neo4jClient
from src.graph.repository import Neo4jRepository
from src.vectorstore.chroma_repository import collection_name
from src.vectorstore.semantic_chroma_repository import semantic_collection_name


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _empty_knowledge() -> dict[str, Any]:
    return {
        "active_version": None,
        "total_items": 0,
        "approved": 0,
        "corrected": 0,
        "pending_review": 0,
        "rejected": 0,
        "items_by_status": {},
        "latest_import": None,
        "sync_jobs": [],
    }


def probe_postgresql(session_factory) -> tuple[dict[str, Any], dict[str, Any]]:
    knowledge = _empty_knowledge()
    session = None

    try:
        session = session_factory()

        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))

        active = session.scalar(
            select(KnowledgeVersionRecord)
            .where(KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE)
            .order_by(KnowledgeVersionRecord.imported_at.desc())
            .limit(1)
        )

        latest_import = session.scalar(
            select(ImportRun)
            .order_by(ImportRun.started_at.desc())
            .limit(1)
        )

        if latest_import is not None:
            knowledge["latest_import"] = {
                "id": str(latest_import.id),
                "status": _enum_value(latest_import.status),
                "requested_knowledge_version": latest_import.requested_knowledge_version,
                "inserted_items": latest_import.inserted_items,
                "started_at": latest_import.started_at,
                "finished_at": latest_import.finished_at,
            }

        if active is not None:
            rows = session.execute(
                select(
                    KnowledgeItem.current_review_status,
                    func.count(),
                )
                .where(KnowledgeItem.knowledge_version_id == active.id)
                .group_by(KnowledgeItem.current_review_status)
            ).all()

            counts = {
                _enum_value(review_status): int(count)
                for review_status, count in rows
            }

            jobs = list(
                session.scalars(
                    select(SyncJob)
                    .where(SyncJob.knowledge_version_id == active.id)
                    .order_by(SyncJob.requested_at.desc())
                )
            )

            knowledge.update(
                {
                    "active_version": active.knowledge_version,
                    "total_items": sum(counts.values()),
                    "approved": counts.get("approved", 0),
                    "corrected": counts.get("corrected", 0),
                    "pending_review": counts.get("pending_review", 0),
                    "rejected": counts.get("rejected", 0),
                    "items_by_status": counts,
                    "sync_jobs": [
                        {
                            "id": str(job.id),
                            "target": _enum_value(job.target),
                            "status": _enum_value(job.status),
                            "attempt_count": job.attempt_count,
                            "requested_at": job.requested_at,
                            "started_at": job.started_at,
                            "finished_at": job.finished_at,
                        }
                        for job in jobs
                    ],
                }
            )

        return (
            {
                "status": "online",
                "active_version": knowledge["active_version"],
            },
            knowledge,
        )

    except Exception:
        return (
            {
                "status": "offline",
                "detail": "No fue posible consultar PostgreSQL.",
            },
            knowledge,
        )
    finally:
        if session is not None:
            try:
                session.rollback()
            finally:
                session.close()


def probe_neo4j() -> dict[str, Any]:
    settings = Neo4jSettings()

    try:
        with Neo4jClient(settings) as client:
            server = client.verify()
            graph = Neo4jRepository(client).status()

        return {
            "status": "online",
            "uri": settings.safe_uri,
            "database": settings.database,
            "server_agent": server.get("agent"),
            "nodes": int(graph.get("nodes", 0)),
            "relationships": int(graph.get("relationships", 0)),
            "versions": graph.get("versions", []),
            "constraints": int(graph.get("constraints", 0)),
        }
    except Exception:
        return {
            "status": "offline",
            "database": settings.database,
            "detail": "No fue posible consultar Neo4j.",
        }


def probe_chroma() -> dict[str, Any]:
    location = Path(
        os.getenv(
            "ERP_ASSISTANT_CHROMA_PATH",
            "data/vectorstore/chroma",
        )
    )

    if not location.exists():
        return {
            "status": "unavailable",
            "collection": collection_name(),
            "documents": 0,
            "detail": "No existe el almacenamiento local de Chroma.",
        }

    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(location))
        collection = client.get_collection(collection_name())

        return {
            "status": "ready",
            "collection": collection_name(),
            "documents": int(collection.count()),
        }
    except Exception:
        return {
            "status": "unavailable",
            "collection": collection_name(),
            "documents": 0,
            "detail": "No fue posible consultar la colección de Chroma.",
        }


def probe_semantic_chroma() -> dict[str, Any]:
    """Probe the dedicated projection that stores approved semantic proposals."""
    location = Path(
        os.getenv(
            "ERP_ASSISTANT_CHROMA_PATH",
            "data/vectorstore/chroma",
        )
    )

    if not location.exists():
        return {
            "status": "unavailable",
            "collection": semantic_collection_name(),
            "documents": 0,
            "detail": "No existe el almacenamiento local de Chroma.",
        }

    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(location))
        collection = client.get_collection(semantic_collection_name())
        return {
            "status": "ready",
            "collection": semantic_collection_name(),
            "documents": int(collection.count()),
        }
    except Exception:
        return {
            "status": "unavailable",
            "collection": semantic_collection_name(),
            "documents": 0,
            "detail": "No fue posible consultar la colección semántica de Chroma.",
        }


def probe_ollama() -> dict[str, Any]:
    settings = OllamaEmbeddingSettings()

    try:
        response = httpx.get(
            f"{settings.url}/api/tags",
            timeout=min(settings.timeout, 5.0),
        )
        response.raise_for_status()
        payload = response.json()

        raw_models = payload.get("models", []) if isinstance(payload, dict) else []
        models = sorted(
            {
                str(model.get("name") or model.get("model"))
                for model in raw_models
                if isinstance(model, dict)
                and (model.get("name") or model.get("model"))
            }
        )

        return {
            "status": "online",
            "configured_embedding_model": settings.model,
            "configured_embedding_model_available": settings.model in models,
            "models": models,
        }
    except Exception:
        return {
            "status": "offline",
            "configured_embedding_model": settings.model,
            "configured_embedding_model_available": False,
            "models": [],
            "detail": "No fue posible consultar Ollama.",
        }


def collect_admin_system_status(session_factory) -> dict[str, Any]:
    postgresql, knowledge = probe_postgresql(session_factory)
    neo4j = probe_neo4j()
    chroma = probe_chroma()
    semantic_chroma = probe_semantic_chroma()
    ollama = probe_ollama()

    services = {
        "postgresql": postgresql,
        "neo4j": neo4j,
        "chroma": chroma,
        "semantic_chroma": semantic_chroma,
        "ollama": ollama,
    }

    ok = (
        postgresql["status"] == "online"
        and neo4j["status"] == "online"
        and chroma["status"] == "ready"
        and ollama["status"] == "online"
    )

    return {
        "ok": ok,
        "generated_at": datetime.now(timezone.utc),
        "services": services,
        "knowledge": knowledge,
    }
