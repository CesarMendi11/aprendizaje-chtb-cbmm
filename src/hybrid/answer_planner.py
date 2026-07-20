from __future__ import annotations

import re
import unicodedata

ABSTAIN = "No encontré conocimiento validado suficiente para responder esa pregunta."


class StructuralAnswerPlanner:
    def __init__(self, aliases=None):
        self.aliases = {
            self._norm(k): tuple(self._norm(value) for value in values)
            for k, values in (aliases or {}).items()
        }

    def plan(self, question, sources, relations, semantic_hits, erp_context=None):
        intent = self._intent(question)
        if not intent:
            return {
                "supported": False,
                "intent": None,
                "answer": None,
                "evidence_ids": [],
                "confidence": "low",
            }
        if intent == "MUTATIVE_ACTION":
            words = set(re.findall(r"[\wáéíóúñ]+", question.casefold()))
            compatible = {
                "cre": ("nuevo", "nueva", "new", "crear", "agregar", "añadir", "registrar"),
                "elimin": ("eliminar", "borrar", "remover"),
                "borr": ("eliminar", "borrar", "remover"),
            }
            evidence = [
                r
                for r in relations
                if r.get("relationship_type") in {"HAS_CONTROL", "HAS_EVENT", "TRIGGERED_BY"}
            ]
            if any(
                any(token.startswith(key) for token in words)
                and any(x in r.get("target_label", "").casefold() for x in vals)
                for key, vals in compatible.items()
                for r in evidence
            ):
                return self._ok(
                    intent,
                    f'La acción está respaldada por el control "{evidence[0]["target_label"]}".',
                    evidence[:1],
                    "high",
                )
            return {
                "supported": False,
                "intent": intent,
                "answer": ABSTAIN,
                "evidence_ids": [],
                "confidence": "high",
            }
        rels = [
            r for r in relations if r.get("source_canonical_id") and r.get("target_canonical_id")
        ]
        focal = self._select_focal_screen(question, sources, rels, semantic_hits)
        if intent == "LIST_FIELDS":
            fields = [
                r
                for r in rels
                if r.get("relationship_type") == "HAS_FIELD"
                and r.get("source_canonical_id") == focal
            ]
            if fields:
                screen = fields[0]["source_label"]
                names = self._unique(r["target_label"] for r in fields)
                controls = [
                    r
                    for r in rels
                    if r.get("relationship_type") == "HAS_CONTROL"
                    and r.get("source_canonical_id") == focal
                ]
                compatible = next(
                    (
                        r
                        for r in controls
                        if self._matches(question, r["target_label"])
                        or any(
                            word in self._norm(r["target_label"])
                            for word in ("buscar", "search", "filtro")
                        )
                    ),
                    None,
                )
                if compatible is None and len(controls) == 1:
                    compatible = controls[0]
                suffix = f', junto al control "{compatible["target_label"]}"' if compatible else ""
                answer = (
                    f'En la pantalla "{screen}" aparecen los campos {self._join(names)}{suffix}.'
                )
                return self._ok(intent, answer, fields, "high")
        if intent == "LOCATE_FIELD":
            fields = [
                r
                for r in rels
                if r.get("relationship_type") == "HAS_FIELD"
                and self._matches(question, r["target_label"])
            ]
            if fields:
                field = fields[0]
                module = next(
                    (
                        r
                        for r in rels
                        if r.get("relationship_type") == "HAS_SCREEN"
                        and r.get("target_canonical_id") == field["source_canonical_id"]
                    ),
                    None,
                )
                answer = f'El campo "{field["target_label"]}" se encuentra en la pantalla "{field["source_label"]}"'  # noqa: E501
                if module:
                    answer += f', dentro del módulo "{module["source_label"]}"'
                return self._ok(
                    intent, answer + ".", [field] + ([module] if module else []), "high"
                )
        if intent == "LOCATE_SCREEN":
            matches = [
                r
                for r in rels
                if r.get("relationship_type") == "HAS_SCREEN"
                and self._matches(question, r["target_label"])
            ]
            if matches:
                r = matches[0]
                return self._ok(
                    intent,
                    f'La pantalla "{r["target_label"]}" está dentro del módulo "{r["source_label"]}".',  # noqa: E501
                    [r],
                    "high",
                )
        if intent == "FIND_CONTROL":
            controls = [
                r
                for r in rels
                if r.get("relationship_type") == "HAS_CONTROL"
                and self._matches(question, r["target_label"])
            ]
            if controls:
                return self._ok(
                    intent,
                    f'El control "{controls[0]["target_label"]}" está en la pantalla "{controls[0]["source_label"]}".',  # noqa: E501
                    controls,
                    "high",
                )
        if intent == "LIST_COLUMNS":
            tables = [
                r
                for r in rels
                if r.get("relationship_type") == "HAS_TABLE"
                and r.get("source_canonical_id") == focal
            ]
            table_ids = {r.get("target_canonical_id") for r in tables}
            cols = [
                r
                for r in rels
                if r.get("relationship_type") == "HAS_COLUMN"
                and r.get("source_canonical_id") in table_ids
            ]
            if cols:
                return self._ok(
                    intent,
                    f'La tabla "{cols[0]["source_label"]}" contiene las columnas {self._join(self._unique(r["target_label"] for r in cols))}.',  # noqa: E501
                    cols,
                    "high",
                )
        if intent == "NAVIGATION_EVENT":
            events = [
                r
                for r in rels
                if r.get("relationship_type")
                in {"HAS_EVENT", "FROM_STATE", "TO_STATE", "TRIGGERED_BY"}
            ]
            if events:
                return self._ok(
                    intent,
                    "La navegación validada incluye eventos y estados relacionados en la pantalla recuperada.",  # noqa: E501
                    events,
                    "medium",
                )
        return {
            "supported": False,
            "intent": intent,
            "answer": None,
            "evidence_ids": [],
            "confidence": "low",
        }

    def _ok(self, intent, answer, evidence, confidence):
        ids = []
        for row in evidence:
            ids.extend([row.get("source_canonical_id"), row.get("target_canonical_id")])
        return {
            "supported": True,
            "intent": intent,
            "answer": answer,
            "evidence_ids": list(dict.fromkeys(i for i in ids if i)),
            "confidence": confidence,
        }

    def _matches(self, question, label):
        q = self._norm(question)
        normalized = self._norm(label)
        aliases = self.aliases.get(normalized, ())
        return normalized in q or any(alias in q for alias in aliases)

    def _select_focal_screen(self, question, sources, relations, semantic_hits):
        screens = {
            s.get("canonical_id"): s
            for s in sources
            if s.get("entity_type") == "screen" and s.get("canonical_id")
        }
        for rel in relations:
            if (
                rel.get("relationship_type") == "HAS_FIELD"
                and rel.get("source_canonical_id") not in screens
            ):
                screens[rel.get("source_canonical_id")] = {
                    "safe_label": rel.get("source_label", "")
                }
        candidates = set(screens)
        for row in semantic_hits or []:
            if row.get("entity_type") == "screen":
                candidates.add(row.get("canonical_id"))
            for rel in relations:
                if row.get("canonical_id") in {
                    rel.get("source_canonical_id"),
                    rel.get("target_canonical_id"),
                } and rel.get("relationship_type") in {
                    "HAS_FIELD",
                    "HAS_CONTROL",
                    "HAS_TABLE",
                    "HAS_SCREEN",
                }:
                    candidates.add(
                        rel.get("source_canonical_id")
                        if rel.get("relationship_type") != "HAS_SCREEN"
                        else rel.get("target_canonical_id")
                    )
        mentioned = [
            cid
            for cid in candidates
            if self._matches(question, screens.get(cid, {}).get("safe_label", ""))
        ]
        ranked = sorted(
            candidates,
            key=lambda cid: (
                -sum(
                    1
                    for r in relations
                    if cid in {r.get("source_canonical_id"), r.get("target_canonical_id")}
                ),
                cid or "",
            ),
        )
        return mentioned[0] if mentioned else (ranked[0] if ranked else None)

    @staticmethod
    def _norm(value):
        text = unicodedata.normalize("NFKD", str(value).casefold())
        text = "".join(char for char in text if not unicodedata.combining(char))
        return " ".join(re.sub(r"[^\w\s]", " ", text).split())

    @staticmethod
    def _intent(question):
        q = question.casefold()
        if re.search(r"\b(elimin|borr|anul|modific|edit|guard|cre|registr|aprob|confirm)", q):
            return "MUTATIVE_ACTION"
        if re.search(r"\b(campo|campos|filtro|filtros)\b", q):
            return "LIST_FIELDS"
        if re.search(r"\b(dónde|donde|ingreso|aparece)\b.*\b(campo|ruc|identificaci)", q):
            return "LOCATE_FIELD"
        if re.search(r"\b(módulo|modulo)\b.*\b(pantalla|dónde|donde)", q):
            return "LOCATE_SCREEN"
        if re.search(r"\b(botón|boton|control)\b", q):
            return "FIND_CONTROL"
        if re.search(r"\b(columnas|columna|tabla)\b", q):
            return "LIST_COLUMNS"
        if re.search(r"\b(página|pagina|avanz|siguiente)\b", q):
            return "NAVIGATION_EVENT"
        return None

    @staticmethod
    def _unique(values):
        return list(dict.fromkeys(values))

    @staticmethod
    def _join(values):
        values = list(values)
        return ", ".join(values[:-1]) + (
            f" y {values[-1]}" if len(values) > 1 else (values[0] if values else "")
        )
