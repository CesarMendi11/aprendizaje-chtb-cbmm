from __future__ import annotations

import hashlib
import os
from pathlib import Path


SEMANTIC_COLLECTION = "erp_assistant_semantic_v1"


def semantic_collection_name() -> str:
    return SEMANTIC_COLLECTION


def semantic_document_id(erp_id: str, knowledge_version: str, semantic_id: str) -> str:
    raw = f"semantic\0{erp_id}\0{knowledge_version}\0{semantic_id}".encode()
    return hashlib.sha256(raw).hexdigest()


class SemanticChromaRepository:
    """Dedicated Chroma projection for human-approved semantic proposals.

    It intentionally uses a separate collection from structural knowledge so a
    structural sync cannot delete or overwrite semantic documents.
    """

    def __init__(self, *, path: str | Path | None = None, client=None):
        if client is None:
            import chromadb

            location = Path(
                path or os.getenv("ERP_ASSISTANT_CHROMA_PATH", "data/vectorstore/chroma")
            )
            client = chromadb.PersistentClient(path=str(location))
        self.client = client
        self.collection = client.get_or_create_collection(
            semantic_collection_name(), metadata={"hnsw:space": "cosine"}
        )

    def sync(self, documents, embeddings, *, erp_id: str, knowledge_version: str):
        documents = list(documents)
        embeddings = list(embeddings)
        if len(documents) != len(embeddings):
            raise ValueError("semantic_document_embedding_count_mismatch")

        ids = [document.id for document in documents]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_semantic_document_id")
        for document in documents:
            metadata = document.metadata
            if (
                metadata.get("erp_id") != erp_id
                or metadata.get("knowledge_version") != knowledge_version
            ):
                raise ValueError("semantic_document_scope_mismatch")

        if ids:
            self.collection.upsert(
                ids=ids,
                documents=[document.text for document in documents],
                metadatas=[document.metadata for document in documents],
                embeddings=embeddings,
            )

        # The semantic collection is an ACTIVE-only physical projection per ERP.
        # Cleaning only the current knowledge_version would leave documents from
        # the previously ACTIVE version behind after a replacement promotion.
        scope = {"erp_id": erp_id}
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
            rows.append(
                {
                    "semantic_id": metadata["semantic_id"],
                    "semantic_type": metadata["semantic_type"],
                    "canonical_id": metadata["canonical_id"],
                    "screen_id": metadata["screen_id"],
                    "screen_route": metadata.get("screen_route"),
                    "safe_label": metadata.get("safe_label") or "Semántica validada",
                    "review_status": metadata["review_status"],
                    "review_revision": int(metadata.get("review_revision", 0)),
                    "evidence_hash": metadata["evidence_hash"],
                    "document": document,
                    "distance": float(distance),
                    "score": max(-1.0, min(1.0, 1.0 - float(distance))),
                }
            )
        return sorted(rows, key=lambda row: row["distance"])
