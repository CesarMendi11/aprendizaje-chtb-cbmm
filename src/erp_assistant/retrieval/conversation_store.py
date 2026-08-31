from __future__ import annotations

import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Iterator
from uuid import uuid4

from .conversation_context import ConversationState


@dataclass(frozen=True)
class _StoredConversation:
    state: ConversationState
    touched_at: float


class ConversationTurn:
    """A serialized turn for one conversation id.

    The store exposes state as an immutable ``ConversationState``. Callers must
    explicitly commit a new governed state; exceptions or fallback paths leave
    the previous state untouched.
    """

    def __init__(self, conversation_id: str, state: ConversationState):
        self.conversation_id = conversation_id
        self.state = state
        self._next_state: ConversationState | None = None

    @property
    def committed_state(self) -> ConversationState | None:
        return self._next_state

    def commit(self, state: ConversationState | dict[str, object]) -> None:
        self._next_state = ConversationState.coerce(state)


class ConversationStateStore:
    """Bounded, process-local store for governed conversation state.

    This is intentionally not an authentication boundary or durable transcript
    store. It keeps only the compact governed ``ConversationState`` produced by
    the hybrid pipeline. A fixed set of striped locks serializes concurrent
    turns for the same conversation without unbounded per-id lock growth.
    """

    def __init__(
        self,
        *,
        max_entries: int = 512,
        ttl_seconds: float = 3600.0,
        lock_stripes: int = 64,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
    ):
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if lock_stripes < 1:
            raise ValueError("lock_stripes must be >= 1")

        self.max_entries = int(max_entries)
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock or time.monotonic
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._entries: OrderedDict[str, _StoredConversation] = OrderedDict()
        self._guard = RLock()
        self._stripes = tuple(RLock() for _ in range(lock_stripes))

    def resolve_conversation_id(self, value: str | None) -> str:
        candidate = str(value or "").strip()
        if candidate:
            return candidate
        generated = str(self._id_factory()).strip()
        if not generated:
            raise RuntimeError("conversation id factory returned an empty value")
        return generated

    def load(self, conversation_id: str) -> ConversationState:
        conversation_id = self.resolve_conversation_id(conversation_id)
        now = self._clock()
        with self._guard:
            self._purge_expired(now)
            entry = self._entries.get(conversation_id)
            if entry is None:
                return ConversationState()
            self._entries.move_to_end(conversation_id)
            self._entries[conversation_id] = _StoredConversation(
                state=entry.state,
                touched_at=now,
            )
            return entry.state

    def save(
        self,
        conversation_id: str,
        state: ConversationState | dict[str, object],
    ) -> ConversationState:
        conversation_id = self.resolve_conversation_id(conversation_id)
        governed = ConversationState.coerce(state)
        now = self._clock()
        with self._guard:
            self._purge_expired(now)
            self._entries[conversation_id] = _StoredConversation(
                state=governed,
                touched_at=now,
            )
            self._entries.move_to_end(conversation_id)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return governed

    @contextmanager
    def turn(self, conversation_id: str | None) -> Iterator[ConversationTurn]:
        resolved_id = self.resolve_conversation_id(conversation_id)
        stripe = self._stripes[hash(resolved_id) % len(self._stripes)]
        with stripe:
            turn = ConversationTurn(resolved_id, self.load(resolved_id))
            yield turn
            if turn.committed_state is not None:
                self.save(resolved_id, turn.committed_state)

    def clear(self, conversation_id: str | None = None) -> None:
        with self._guard:
            if conversation_id is None:
                self._entries.clear()
                return
            self._entries.pop(str(conversation_id).strip(), None)

    @property
    def size(self) -> int:
        now = self._clock()
        with self._guard:
            self._purge_expired(now)
            return len(self._entries)

    def _purge_expired(self, now: float) -> None:
        expired = [
            conversation_id
            for conversation_id, entry in self._entries.items()
            if now - entry.touched_at >= self.ttl_seconds
        ]
        for conversation_id in expired:
            self._entries.pop(conversation_id, None)
