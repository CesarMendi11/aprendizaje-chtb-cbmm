from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from src.crawler.state_signature import StateSignature
from src.models.crawl_path import CrawlPath
from src.models.ui_state import UIState


@dataclass(frozen=True)
class StateRegistration:
    """Resultado de registrar un estado en ``StateRegistry``."""

    state: UIState
    is_new: bool
    path_updated: bool = False


class StateRegistry:
    """
    Registro determinístico de estados funcionales de interfaz.

    La identidad canónica se basa en ``structural_signature``. La firma ya
    incluye la ruta normalizada, por lo que el mismo DOM funcional en dos rutas
    distintas no se colapsa accidentalmente.

    Si un estado se descubre nuevamente mediante una trayectoria más corta, se
    conserva esa trayectoria sin alterar su identidad.
    """

    STATE_ID_PREFIX = "ui_state"

    def __init__(self):
        self._states_by_id: dict[str, UIState] = {}
        self._state_id_by_signature: dict[str, str] = {}
        self._exact_signatures: dict[str, set[str]] = {}

    @classmethod
    def build_state_id(cls, structural_signature: str) -> str:
        if not structural_signature:
            raise ValueError("structural_signature no puede estar vacía.")
        return f"{cls.STATE_ID_PREFIX}:{structural_signature}"

    def create_state(
        self,
        signature: StateSignature,
        path: CrawlPath | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UIState:
        state_id = self.build_state_id(signature.structural_fingerprint)
        return UIState(
            state_id=state_id,
            route=signature.route,
            title=signature.title,
            exact_signature=signature.exact_fingerprint,
            structural_signature=signature.structural_fingerprint,
            summary=signature.summary,
            path=path,
            metadata=metadata or {},
        )

    def register(self, state: UIState) -> StateRegistration:
        if not state.state_id:
            raise ValueError("state.state_id no puede estar vacío.")
        if not state.structural_signature:
            raise ValueError("state.structural_signature no puede estar vacía.")

        existing_id = self._state_id_by_signature.get(state.structural_signature)
        if existing_id is None:
            self._states_by_id[state.state_id] = state
            self._state_id_by_signature[state.structural_signature] = state.state_id
            self._exact_signatures[state.state_id] = {state.exact_signature}
            return StateRegistration(state=state, is_new=True)

        existing = self._states_by_id[existing_id]
        self._exact_signatures.setdefault(existing_id, set()).add(
            state.exact_signature
        )

        if self._is_shorter_path(state.path, existing.path):
            updated = replace(
                existing,
                path=state.path,
                metadata=self._merge_metadata(existing.metadata, state.metadata),
            )
            self._states_by_id[existing_id] = updated
            return StateRegistration(
                state=updated,
                is_new=False,
                path_updated=True,
            )

        if state.metadata:
            merged = self._merge_metadata(existing.metadata, state.metadata)
            if merged != existing.metadata:
                existing = replace(existing, metadata=merged)
                self._states_by_id[existing_id] = existing

        return StateRegistration(state=existing, is_new=False)

    def register_signature(
        self,
        signature: StateSignature,
        path: CrawlPath | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StateRegistration:
        return self.register(
            self.create_state(
                signature=signature,
                path=path,
                metadata=metadata,
            )
        )

    def get(self, state_id: str) -> UIState | None:
        return self._states_by_id.get(state_id)

    def require(self, state_id: str) -> UIState:
        state = self.get(state_id)
        if state is None:
            raise KeyError(f"No existe el estado: {state_id}")
        return state

    def find_by_signature(self, structural_signature: str) -> UIState | None:
        state_id = self._state_id_by_signature.get(structural_signature)
        if state_id is None:
            return None
        return self._states_by_id[state_id]

    def contains(self, state_id: str) -> bool:
        return state_id in self._states_by_id

    def count(self) -> int:
        return len(self._states_by_id)

    def states(self) -> list[UIState]:
        return list(self._states_by_id.values())

    def exact_signatures_for(self, state_id: str) -> set[str]:
        return set(self._exact_signatures.get(state_id, set()))

    def to_dict(self) -> dict[str, Any]:
        states = []
        for state in self._states_by_id.values():
            payload = state.to_dict()
            payload["observed_exact_signatures"] = sorted(
                self._exact_signatures.get(state.state_id, set())
            )
            states.append(payload)

        return {
            "registry_type": "ui_state_registry",
            "states": states,
            "summary": {"states_count": len(states)},
        }

    def __iter__(self) -> Iterable[UIState]:
        return iter(self._states_by_id.values())

    @staticmethod
    def _is_shorter_path(
        candidate: CrawlPath | None,
        current: CrawlPath | None,
    ) -> bool:
        if candidate is None:
            return False
        if current is None:
            return True
        return candidate.depth < current.depth

    @staticmethod
    def _merge_metadata(
        current: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(current)
        for key, value in incoming.items():
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value
        return merged
