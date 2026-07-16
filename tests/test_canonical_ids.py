from src.knowledge.canonical.ids import stable_id


def test_ids_are_stable_and_distinct():
    first = stable_id("screen", "erp:1", "/app/products")
    assert first == stable_id("screen", "erp:1", "/app/products")
    assert first != stable_id("screen", "erp:1", "/app/suppliers")


def test_fixture_ids_have_no_collisions():
    ids = {stable_id("field", "screen", "label", pos) for pos in range(50)}
    assert len(ids) == 50
