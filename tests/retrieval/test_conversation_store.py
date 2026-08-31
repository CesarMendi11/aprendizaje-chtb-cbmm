from __future__ import annotations

from erp_assistant.retrieval.conversation_context import ConversationEntity, ConversationState
from erp_assistant.retrieval.conversation_store import ConversationStateStore


def state(label: str, turn: int) -> ConversationState:
    return ConversationState(
        erp_id="erp:test",
        knowledge_version="kv:test",
        current_screen=ConversationEntity(
            canonical_id=f"screen:{label.casefold()}",
            entity_type="screen",
            safe_label=label,
            route=f"/admin/{label.casefold()}",
        ),
        turn_index=turn,
    )


def test_turn_requires_explicit_commit():
    store = ConversationStateStore(id_factory=lambda: "generated")

    with store.turn("conv") as turn:
        assert turn.state == ConversationState()

    assert store.load("conv") == ConversationState()

    expected = state("Año", 1)
    with store.turn("conv") as turn:
        turn.commit(expected)

    assert store.load("conv") == expected


def test_generated_ids_are_returnable_and_isolated():
    generated = iter(["conv-a", "conv-b"])
    store = ConversationStateStore(id_factory=lambda: next(generated))

    first = store.resolve_conversation_id(None)
    second = store.resolve_conversation_id(None)

    assert first == "conv-a"
    assert second == "conv-b"

    store.save(first, state("Año", 1))
    assert store.load(second) == ConversationState()


def test_store_is_bounded_by_lru_capacity():
    store = ConversationStateStore(max_entries=2)

    store.save("a", state("A", 1))
    store.save("b", state("B", 1))
    assert store.load("a").current_screen.safe_label == "A"

    store.save("c", state("C", 1))

    assert store.load("b") == ConversationState()
    assert store.load("a").current_screen.safe_label == "A"
    assert store.load("c").current_screen.safe_label == "C"


def test_store_expires_inactive_state():
    now = [100.0]
    store = ConversationStateStore(
        ttl_seconds=10,
        clock=lambda: now[0],
    )

    store.save("conv", state("Año", 1))
    now[0] = 109.0
    assert store.load("conv").current_screen.safe_label == "Año"

    now[0] = 120.0
    assert store.load("conv") == ConversationState()
    assert store.size == 0


def test_same_conversation_turn_sees_committed_state():
    store = ConversationStateStore()
    first = state("Año", 1)
    second = state("Dashboard", 2)

    with store.turn("conv") as turn:
        turn.commit(first)

    with store.turn("conv") as turn:
        assert turn.state == first
        turn.commit(second)

    assert store.load("conv") == second
