from types import SimpleNamespace

from src.vectorstore.semantic_chroma_repository import (
    SemanticChromaRepository,
    semantic_collection_name,
    semantic_document_id,
)


class FakeCollection:
    def __init__(self):
        self.upserts = []
        self.deleted = []
        self.current_ids = []

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def get(self, *, where, include):
        return {"ids": list(self.current_ids)}

    def delete(self, *, ids):
        self.deleted.extend(ids)


class FakeClient:
    def __init__(self):
        self.collection = FakeCollection()
        self.requested_name = None
        self.metadata = None

    def get_or_create_collection(self, name, metadata):
        self.requested_name = name
        self.metadata = metadata
        return self.collection


def test_semantic_repository_uses_dedicated_collection_and_scope():
    client = FakeClient()
    repository = SemanticChromaRepository(client=client)
    assert client.requested_name == "erp_assistant_semantic_v1"
    assert semantic_collection_name() == "erp_assistant_semantic_v1"

    keep_id = semantic_document_id("erp:test", "v1", "semantic:keep")
    stale_id = semantic_document_id("erp:test", "v1", "semantic:stale")
    client.collection.current_ids = [keep_id, stale_id]
    document = SimpleNamespace(
        id=keep_id,
        text="Propósito: prueba",
        metadata={"erp_id": "erp:test", "knowledge_version": "v1"},
    )
    changed, removed = repository.sync(
        [document], [[1.0, 0.0]], erp_id="erp:test", knowledge_version="v1"
    )
    assert changed == 1
    assert removed == 1
    assert client.collection.deleted == [stale_id]
    assert client.collection.upserts[0]["ids"] == [keep_id]
