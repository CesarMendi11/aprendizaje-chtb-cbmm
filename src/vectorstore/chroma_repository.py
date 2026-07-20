from __future__ import annotations

import hashlib
import os
from pathlib import Path


def collection_name() -> str:
    return "erp_assistant_knowledge_v1"


def document_id(erp_id: str, knowledge_version: str, canonical_id: str) -> str:
    raw = f"{erp_id}\0{knowledge_version}\0{canonical_id}".encode()
    return hashlib.sha256(raw).hexdigest()


class ChromaRepository:
    def __init__(self, *, path: str | Path | None = None, client=None):
        if client is None:
            import chromadb

            location = Path(
                path or os.getenv("ERP_ASSISTANT_CHROMA_PATH", "data/vectorstore/chroma")
            )
            client = chromadb.PersistentClient(path=str(location))
        self.client = client
        self.collection = client.get_or_create_collection(
            collection_name(), metadata={"hnsw:space": "cosine"}
        )

    def sync(self, documents, embeddings, *, erp_id: str, knowledge_version: str):
        ids = [document.id for document in documents]
        if ids:
            self.collection.upsert(
                ids=ids,
                documents=[document.text for document in documents],
                metadatas=[document.metadata for document in documents],
                embeddings=embeddings,
            )
        scope = {"$and": [{"erp_id": erp_id}, {"knowledge_version": knowledge_version}]}
        current = self.collection.get(where=scope, include=[])["ids"]
        stale = sorted(set(current) - set(ids))
        if stale:
            self.collection.delete(ids=stale)
        return len(ids), len(stale)

    def query(self, embedding, *, top_k=5, erp_id=None, knowledge_version=None):
        clauses = []
        if erp_id:
            clauses.append({"erp_id": erp_id})
        if knowledge_version:
            clauses.append({"knowledge_version": knowledge_version})
        where = None
        if len(clauses) == 1:
            where = clauses[0]
        elif clauses:
            where = {"$and": clauses}
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where=where,
            include=["metadatas", "documents", "distances"],
        )
        rows = []
        for metadata, document, distance in zip(
            result["metadatas"][0],
            result["documents"][0],
            result["distances"][0],
            strict=True,
        ):
            safe_label = metadata.get("safe_label")
            if not safe_label:
                safe_label = next(
                    (line.split(":", 1)[1].strip() for line in document.splitlines()
                     if line.startswith("Nombre:") and ":" in line),
                    "Entidad de conocimiento",
                )
            rows.append(
                {
                    "canonical_id": metadata["canonical_id"],
                    "entity_type": metadata["entity_type"],
                    "safe_label": safe_label,
                    "screen_route": metadata.get("screen_route"),
                    "review_status": metadata["review_status"],
                    "distance": float(distance),
                    "score": max(-1.0, min(1.0, 1.0 - float(distance))),
                }
            )
        return sorted(rows, key=lambda row: row["distance"])
