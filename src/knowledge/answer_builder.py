from __future__ import annotations

from enum import StrEnum

from src.knowledge.models import StructuralScreen
from src.knowledge.structural_search_service import SearchResponse
from src.knowledge.text_normalizer import normalize_text, tokens


class Intent(StrEnum):
    LOCATE = "locate"
    DESCRIBE = "describe"
    FIELDS = "fields"
    BUTTONS = "buttons"
    TABLES = "tables"
    UNKNOWN = "unknown"


NOT_FOUND = (
    "No encontré información validada para responder esa consulta. "
    "Puedes preguntarme por una pantalla, campo, botón, tabla o ruta del ERP."
)


class AnswerBuilder:
    def build(self, question: str, search: SearchResponse) -> tuple[str, str, list[str]]:
        if search.best is None:
            return NOT_FOUND, "not_found", []
        screen = search.best.screen
        intent = self.detect_intent(question)
        if intent is Intent.UNKNOWN:
            return NOT_FOUND, "not_found", []
        answer = self._answer(intent, screen)
        if not answer:
            return NOT_FOUND, "not_found", []
        return answer, "answered", self._suggestions(screen, intent)

    @staticmethod
    def detect_intent(question: str) -> Intent:
        normalized = normalize_text(question)
        query_tokens = tokens(question, remove_stop_words=False)
        if {"campo", "campos"} & query_tokens:
            return Intent.FIELDS
        if {"boton", "botones", "accion", "acciones"} & query_tokens:
            return Intent.BUTTONS
        if {"tabla", "tablas", "columnas", "resultados"} & query_tokens:
            return Intent.TABLES
        if any(
            term in normalized
            for term in ("que puedo hacer", "para que sirve", "que contiene", "describe")
        ):
            return Intent.DESCRIBE
        if any(
            term in normalized
            for term in ("donde", "como reviso", "como consulto", "como encuentro", "ruta")
        ):
            return Intent.LOCATE
        return Intent.LOCATE if len(tokens(question)) <= 4 else Intent.UNKNOWN

    def _answer(self, intent: Intent, screen: StructuralScreen) -> str | None:
        title = screen.display_title
        if intent is Intent.LOCATE:
            return f"Puedes encontrar {title} en la ruta {self._breadcrumb(screen.route, title)}."
        if intent is Intent.FIELDS:
            names = screen.field_names
            return (
                f"En la pantalla {title} se identificaron los campos: {self._join(names)}."
                if names
                else None
            )
        if intent is Intent.BUTTONS:
            names = screen.button_names
            return (
                f"En la pantalla {title} se identificaron los botones visibles: "
                f"{self._join(names)}."
                if names
                else None
            )
        if intent is Intent.TABLES:
            headers = screen.table_headers
            return (
                f"La tabla de {title} contiene las columnas: {self._join(headers)}."
                if headers
                else None
            )
        if intent is Intent.DESCRIBE:
            parts = []
            if screen.field_names:
                parts.append(f"{len(screen.field_names)} campos")
            if screen.button_names:
                count = len(screen.button_names)
                parts.append(f"{count} " + ("botón visible" if count == 1 else "botones visibles"))
            if screen.tables:
                parts.append(
                    f"{len(screen.tables)} tabla" + ("s" if len(screen.tables) != 1 else "")
                )
            if screen.link_names:
                count = len(screen.link_names)
                parts.append(f"{count} " + ("enlace local" if count == 1 else "enlaces locales"))
            return f"La pantalla {title} contiene {self._join(parts)}." if parts else None
        return None

    @staticmethod
    def _breadcrumb(route: str, title: str) -> str:
        segments = [
            segment.replace("-", " ").replace("x", " por ").capitalize()
            for segment in route.strip("/").split("/")[1:-1]
        ]
        return " → ".join([*segments, title]) if segments else title

    @staticmethod
    def _join(items: list[str]) -> str:
        if len(items) < 2:
            return "".join(items)
        return ", ".join(items[:-1]) + f" y {items[-1]}"

    @staticmethod
    def _suggestions(screen: StructuralScreen, intent: Intent) -> list[str]:
        options = []
        if intent is not Intent.FIELDS and screen.field_names:
            options.append(f"¿Qué campos tiene la pantalla {screen.display_title}?")
        if intent is not Intent.TABLES and screen.tables:
            options.append(f"¿Qué tabla tiene la pantalla {screen.display_title}?")
        options.append("¿Cómo regreso al Dashboard?")
        return options[:2]
