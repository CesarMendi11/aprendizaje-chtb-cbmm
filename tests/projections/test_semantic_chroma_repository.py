from types import SimpleNamespace

import pytest

from src.vectorstore.semantic_chroma_repository import (
    SemanticChromaRepository,
    semantic_collection_name,
    semantic_document_id,
)


class FakeCollection:
    def __init__(self):
        self.upserts = []
        self.deleted = []
        self.records = {}
        self.get_calls = []

    def seed(self, document_id, *, erp_id, knowledge_version):
        self.records[document_id] = {
            "erp_id": erp_id,
            "knowledge_version": knowledge_version,
        }

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        for document_id, metadata in zip(
            kwargs["ids"], kwargs["metadatas"], strict=True
        ):
            self.records[document_id] = dict(metadata)

    def get(self, *, where, include):
        self.get_calls.append({"where": where, "include": include})
        if set(where) != {"erp_id"}:
            raise AssertionError(f"Unexpected semantic cleanup scope: {where}")
        return {
            "ids": [
                document_id
                for document_id, metadata in self.records.items()
                if metadata.get("erp_id") == where["erp_id"]
            ]
        }

    def delete(self, *, ids):
        self.deleted.extend(ids)
        for document_id in ids:
            self.records.pop(document_id, None)


class FakeClient:
    def __init__(self):
        self.collection = FakeCollection()
        self.requested_name = None
        self.metadata = None

    def get_or_create_collection(self, name, metadata):
        self.requested_name = name
        self.metadata = metadata
        return self.collection


def document(erp_id, knowledge_version, semantic_id):
    return SimpleNamespace(
        id=semantic_document_id(erp_id, knowledge_version, semantic_id),
        text="Propósito: prueba",
        metadata={
            "erp_id": erp_id,
            "knowledge_version": knowledge_version,
            "semantic_id": semantic_id,
        },
    )


def test_semantic_repository_replaces_all_semantic_documents_for_same_erp_only():
    client = FakeClient()
    repository = SemanticChromaRepository(client=client)
    assert client.requested_name == "erp_assistant_semantic_v1"
    assert semantic_collection_name() == "erp_assistant_semantic_v1"

    old_active_id = semantic_document_id("erp:test", "v1", "semantic:old")
    stale_current_id = semantic_document_id("erp:test", "v2", "semantic:stale")
    other_erp_id = semantic_document_id("erp:other", "v9", "semantic:other")
    client.collection.seed(old_active_id, erp_id="erp:test", knowledge_version="v1")
    client.collection.seed(stale_current_id, erp_id="erp:test", knowledge_version="v2")
    client.collection.seed(other_erp_id, erp_id="erp:other", knowledge_version="v9")

    keep = document("erp:test", "v2", "semantic:keep")
    changed, removed = repository.sync(
        [keep], [[1.0, 0.0]], erp_id="erp:test", knowledge_version="v2"
    )

    assert changed == 1
    assert removed == 2
    assert client.collection.get_calls == [
        {"where": {"erp_id": "erp:test"}, "include": []}
    ]
    assert client.collection.deleted == sorted([old_active_id, stale_current_id])
    assert keep.id in client.collection.records
    assert other_erp_id in client.collection.records
    assert client.collection.upserts[0]["ids"] == [keep.id]


def test_semantic_repository_rejects_documents_outside_requested_scope():
    repository = SemanticChromaRepository(client=FakeClient())
    wrong = document("erp:other", "v2", "semantic:wrong")

    with pytest.raises(ValueError, match="semantic_document_scope_mismatch"):
        repository.sync(
            [wrong], [[1.0, 0.0]], erp_id="erp:test", knowledge_version="v2"
        )
