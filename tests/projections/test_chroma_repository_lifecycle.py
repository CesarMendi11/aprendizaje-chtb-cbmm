from __future__ import annotations

from src.vectorstore import ChromaRepository


class FakeCollection:
    def __init__(self):
        self.deleted = []
        self.scopes = []

    def get(self, *, where, include):
        self.scopes.append((where, include))
        return {"ids": ["doc:1", "doc:2"]}

    def delete(self, *, ids):
        self.deleted.append(list(ids))


class FakeClient:
    def __init__(self):
        self.collection = FakeCollection()

    def get_or_create_collection(self, _name, metadata):
        assert metadata == {"hnsw:space": "cosine"}
        return self.collection


def test_delete_version_is_strictly_namespaced():
    client = FakeClient()
    repository = ChromaRepository(client=client)

    removed = repository.delete_version(
        erp_id="erp:test",
        knowledge_version="v1",
    )

    assert removed == 2
    assert client.collection.scopes == [
        (
            {
                "$and": [
                    {"erp_id": "erp:test"},
                    {"knowledge_version": "v1"},
                ]
            },
            [],
        )
    ]
    assert client.collection.deleted == [["doc:1", "doc:2"]]
