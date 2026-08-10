from __future__ import annotations

import re
from collections import OrderedDict

from sqlalchemy import select

from src.database.models import KnowledgeItem
from src.database.services import ChromaSyncService, SemanticRetrievalAuthorizationService
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.privacy import sanitize_text

from .answer_planner import StructuralAnswerPlanner

ALLOWED_RELATIONSHIPS = {
    "HAS_MODULE",
    "HAS_SCREEN",
    "HAS_STATE",
    "HAS_FIELD",
    "HAS_CONTROL",
    "HAS_TABLE",
    "HAS_COLUMN",
    "HAS_LINK",
    "HAS_EVENT",
    "FROM_STATE",
    "TO_STATE",
    "TRIGGERED_BY",
}
ABSTAIN = "No encontré conocimiento validado suficiente para responder esa pregunta."
SYSTEM_PROMPT = (
    "Responde en español usando exclusivamente el contexto validado. No inventes "
    "pantallas, botones ni pasos. Abstente si el contexto no basta."
)
MUTATIVE_FORMS = {
    "eliminar": r"elimin(?:ar|o|a|e|é|ando|ado)",
    "borrar": r"borr(?:ar|o|a|e|é|ando|ado)",
    "anular": r"anul(?:ar|o|a|e|é|ando|ado)",
    "modificar": r"modific(?:ar|o|a|e|ando|ado)",
    "editar": r"edit(?:ar|o|a|e|ando|ado)",
    "guardar": r"guard(?:ar|o|a|e|ando|ado)",
    "crear": r"cre(?:ar|o|a|e|ando|ado)",
    "registrar": r"registr(?:ar|o|a|e|ando|ado)",
    "aprobar": r"aprob(?:ar|o|a|e|ando|ado)",
    "confirmar": r"confirm(?:ar|o|a|e|ando|ado)",
}
TYPE_NAMES = {
    "erp_system": "ERP",
    "module": "módulo",
    "screen": "pantalla",
    "ui_state": "estado",
    "field": "campo",
    "control": "control",
    "table": "tabla",
    "table_column": "columna",
    "event": "evento",
    "transition": "transición",
    "link": "enlace",
}


class HybridKnowledgeRetriever:
    def __init__(
        self,
        session,
        *,
        chroma,
        neo4j,
        embeddings,
        semantic_chroma=None,
        semantic_authorizer=None,
        generator=None,
        planner=None,
        aliases=None,
    ):
        self.session, self.chroma, self.neo4j = session, chroma, neo4j
        self.semantic_chroma = semantic_chroma
        self.embeddings, self.generator = embeddings, generator
        self.semantic_authorizer = semantic_authorizer or (
            SemanticRetrievalAuthorizationService(session) if session is not None else None
        )
        self.planner = planner or StructuralAnswerPlanner(aliases)

    def retrieve(
        self, question, *, erp_id=None, knowledge_version=None, semantic_top_k=8, graph_limit=20
    ):
        version, _, _ = ChromaSyncService(self.session).prepare(
            erp_id=erp_id, knowledge_version=knowledge_version
        )
        erp_id, knowledge_version = version.erp_id, version.knowledge_version
        query_embedding = self.embeddings.embed(question)[0]
        semantic = self.chroma.query(
            query_embedding,
            top_k=semantic_top_k,
            erp_id=erp_id,
            knowledge_version=knowledge_version,
        )
        semantic_candidates = (
            self.semantic_chroma.query(
                query_embedding,
                top_k=semantic_top_k,
                erp_id=erp_id,
                knowledge_version=knowledge_version,
            )
            if self.semantic_chroma is not None
            else []
        )
        approved_semantics = (
            self.semantic_authorizer.authorize_hits(semantic_candidates, version=version)
            if self.semantic_authorizer is not None
            else []
        )
        seeds = list(
            OrderedDict.fromkeys(
                [row["canonical_id"] for row in semantic]
                + [row["screen_id"] for row in approved_semantics]
            )
        )
        neighbors = self._expand(seeds, erp_id, knowledge_version, graph_limit)
        ids = self._candidate_ids(seeds, neighbors)
        valid = {i.canonical_id: i for i in self._validate(ids, version.id)}

        # If the question explicitly names a validated screen, complete a focused
        # two-hop expansion from that screen. The initial semantic top-k can be
        # dominated by UI states/fields and the global graph limit may otherwise
        # omit sibling fields or table columns needed by deterministic answers.
        focused_screen_ids = []
        for cid, item in valid.items():
            if item.entity_type != "screen":
                continue
            payload = self._effective(item.id)
            label = self._label(item.entity_type, payload)
            if self.planner._matches(question, label):
                focused_screen_ids.append(cid)
        if focused_screen_ids:
            focused_neighbors = self._expand(
                focused_screen_ids,
                erp_id,
                knowledge_version,
                max(graph_limit, 64),
            )
            neighbors = self._merge_neighbors(neighbors, focused_neighbors)
            ids = self._candidate_ids(seeds, neighbors)
            valid = {i.canonical_id: i for i in self._validate(ids, version.id)}
        semantic_by_id = {row["canonical_id"]: row for row in semantic}
        approved_semantics_by_screen = {row["screen_id"]: row for row in approved_semantics}
        graph_ids = {n["canonical_id"] for n in neighbors}
        sources = []
        for cid in ids:
            item = valid.get(cid)
            if not item:
                continue
            hit = semantic_by_id.get(cid)
            approved_semantic = approved_semantics_by_screen.get(cid)
            payload = self._effective(item.id)
            if approved_semantic and hit and cid in graph_ids:
                origin = "structural_semantic+approved_semantic+graph"
            elif approved_semantic and cid in graph_ids:
                origin = "approved_semantic+graph"
            elif hit and cid in graph_ids:
                origin = "semantic+graph"
            elif approved_semantic:
                origin = "approved_semantic"
            else:
                origin = "semantic" if hit else "graph"
            sources.append(
                {
                    "canonical_id": cid,
                    "entity_type": item.entity_type,
                    "safe_label": self._label(item.entity_type, payload),
                    "screen_route": item.route,
                    "origin": origin,
                    "score": (
                        approved_semantic.get("score")
                        if approved_semantic is not None
                        else (hit.get("score") if hit else None)
                    ),
                }
            )
        relations = self._relations(neighbors, valid)
        route_by_screen = {
            s["canonical_id"]: s.get("screen_route")
            for s in sources
            if s["entity_type"] == "screen" and s.get("screen_route")
        }
        table_screen = {
            r["target_canonical_id"]: r["source_canonical_id"]
            for r in relations
            if r["relationship_type"] == "HAS_TABLE"
        }
        for source in sources:
            if source.get("screen_route"):
                continue
            relation = next(
                (
                    r
                    for r in relations
                    if r["target_canonical_id"] == source["canonical_id"]
                    and r["relationship_type"]
                    in {"HAS_FIELD", "HAS_CONTROL", "HAS_TABLE", "HAS_STATE", "HAS_EVENT"}
                ),
                None,
            )
            if relation:
                source["screen_route"] = route_by_screen.get(relation["source_canonical_id"])
                continue
            column_relation = next(
                (
                    r
                    for r in relations
                    if r["target_canonical_id"] == source["canonical_id"]
                    and r["relationship_type"] == "HAS_COLUMN"
                ),
                None,
            )
            if column_relation:
                screen_id = table_screen.get(column_relation["source_canonical_id"])
                source["screen_route"] = route_by_screen.get(screen_id)
        return {
            "status": "ok",
            "question": question,
            "erp_id": erp_id,
            "knowledge_version": knowledge_version,
            "retrieval": {
                "semantic_hits": len(semantic),
                "semantic_candidates": len(semantic_candidates),
                "approved_semantic_hits": len(approved_semantics),
                "graph_neighbors": len(neighbors),
                "validated_items": len(sources),
            },
            "sources": sources[:10],
            "relations": relations,
            "approved_semantics": approved_semantics,
            "context": self._context(sources, relations, semantic, approved_semantics),
        }

    def ask(self, question, *, generate=True, **kwargs):
        result = self.retrieve(question, **kwargs)
        plan = self.planner.plan(
            question,
            result["sources"],
            result.get("relations", []),
            result["sources"],
            approved_semantics=result.get("approved_semantics", []),
        )
        result["intent"] = plan.get("intent")
        result["confidence"] = plan.get("confidence")
        result["evidence_ids"] = plan.get("evidence_ids", [])
        result["answer_mode"] = "insufficient_evidence"
        if plan["supported"]:
            result["answer"] = plan["answer"]
            result["answer_mode"] = plan.get("answer_mode", "deterministic_graph")
            result["evidence_ids"] = plan["evidence_ids"]
            result.pop("context", None)
            return result
        if plan["intent"] == "MUTATIVE_ACTION":
            result["answer"] = ABSTAIN
            result["answer_mode"] = "policy_abstention"
            result.pop("context", None)
            return result
        if (
            not result["context"]
            or not result["sources"]
            or self._needs_abstention(question, result)
        ):
            result["answer"] = ABSTAIN
        elif not generate or not self.generator:
            result["answer"] = None
            result["answer_mode"] = "ollama_grounded"
        else:
            prompt = (
                f"Pregunta del usuario:\n{question}\n\nContexto validado:\n"
                f"{result['context']}\n\nResponde únicamente con información respaldada "
                f"explícitamente por el contexto. Puedes interpretar abreviaturas y sinónimos "
                f"comunes, pero no inventes estructura ni procedimientos. Si no basta, responde "
                f"exactamente:\n{ABSTAIN}"
            )
            generated_answer = self.generator.generate(prompt, system=SYSTEM_PROMPT)
            result["answer"] = generated_answer
            if not self._is_abstention(generated_answer):
                result["answer_mode"] = "ollama_grounded"
        if generate:
            result.pop("context", None)
        return result

    @staticmethod
    def _merge_neighbors(*groups):
        merged = []
        seen = set()
        for group in groups:
            for row in group:
                edge_key = tuple(
                    (
                        edge.get("relationship_type"),
                        edge.get("from_canonical_id"),
                        edge.get("to_canonical_id"),
                    )
                    for edge in row.get("path_edges", [])
                )
                key = (row.get("source_canonical_id"), row.get("canonical_id"), edge_key)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(row)
        return merged

    @staticmethod
    def _is_abstention(answer):
        text = str(answer or "").strip().casefold()
        if text == ABSTAIN.casefold():
            return True
        return bool(
            re.match(
                r"^no\s+encontr[eé]\s+conocimiento\s+validado\s+suficiente\b",
                text,
            )
        )

    @staticmethod
    def _candidate_ids(seeds, neighbors):
        endpoint_ids = [
            row.get("canonical_id")
            for row in neighbors
            if row.get("canonical_id")
        ]
        path_ids = [
            node_id
            for row in neighbors
            for edge in row.get("path_edges", [])
            for node_id in (
                edge.get("from_canonical_id"),
                edge.get("to_canonical_id"),
            )
            if node_id
        ]
        return list(
            OrderedDict.fromkeys(
                list(seeds) + endpoint_ids + path_ids
            )
        )

    def _expand(self, seeds, erp_id, version, limit):
        if not seeds:
            return []
        query = (
            "MATCH p=(a)-[*1..2]-(b) WHERE a.canonical_id IN $seeds "
            "AND a.erp_id=$erp_id AND a.knowledge_version=$version "
            "AND b.canonical_id <> a.canonical_id AND b.erp_id=$erp_id "
            "AND b.knowledge_version=$version "
            "AND all(rel IN relationships(p) WHERE type(rel) IN $rels) "
            "WITH a,b,p ORDER BY length(p), b.canonical_id, a.canonical_id LIMIT $limit "
            "RETURN a.canonical_id AS source_canonical_id, "
            "b.canonical_id AS canonical_id, b.entity_type AS entity_type, "
            "[rel IN relationships(p) | {relationship_type: type(rel), "
            "from_canonical_id: startNode(rel).canonical_id, "
            "to_canonical_id: endNode(rel).canonical_id}] AS path_edges"
        )
        return self.neo4j.execute(
            query,
            {
                "seeds": seeds,
                "erp_id": erp_id,
                "version": version,
                "rels": sorted(ALLOWED_RELATIONSHIPS),
                "limit": limit,
            },
        )

    def _validate(self, ids, version_id):
        if not ids:
            return []

        query = select(KnowledgeItem).where(
            KnowledgeItem.knowledge_version_id == version_id,
            KnowledgeItem.canonical_id.in_(ids),
            KnowledgeItem.current_review_status.in_(
                [ReviewStatus.APPROVED, ReviewStatus.CORRECTED]
            ),
        )

        items = list(self.session.scalars(query))
        by_id = {item.canonical_id: item for item in items}

        return [by_id[cid] for cid in ids if cid in by_id]

    def _effective(self, item_id):
        return ChromaSyncService(self.session).effective.describe(item_id)["effective_payload"]

    @staticmethod
    def _label(entity_type, payload):
        keys = {
            "screen": ("title",),
            "field": ("label", "name"),
            "control": ("label",),
            "table": ("name",),
            "table_column": ("name",),
            "module": ("name",),
        }.get(entity_type, ("label", "name", "title"))
        for key in keys:
            value, detections = sanitize_text(payload.get(key), 240)
            if value and not detections:
                return value
        return "Entidad validada"

    def _relations(self, neighbors, valid):
        out, seen = [], set()
        for row in neighbors:
            edges = row.get("path_edges", [])
            path = [
                node
                for edge in edges
                for node in (edge.get("from_canonical_id"), edge.get("to_canonical_id"))
            ]
            if (
                not edges
                or row.get("canonical_id") not in valid
                or not path
                or not all(node in valid for node in path)
            ):
                continue
            for edge in edges:
                source, target, rel_type = (
                    edge.get("from_canonical_id"),
                    edge.get("to_canonical_id"),
                    edge.get("relationship_type"),
                )
                key = (source, rel_type, target)
                if source not in valid or target not in valid or not rel_type or key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "source_canonical_id": source,
                        "target_canonical_id": target,
                        "relationship_type": rel_type,
                        "source_label": self._label(
                            valid[source].entity_type, self._effective(valid[source].id)
                        ),
                        "target_label": self._label(
                            valid[target].entity_type, self._effective(valid[target].id)
                        ),
                        "source_type": valid[source].entity_type,
                        "target_type": valid[target].entity_type,
                    }
                )
        return out

    @staticmethod
    def _context(sources, relations, semantic, approved_semantics=None):
        entities = "\n".join(f"- {s['entity_type']}: {s['safe_label']}" for s in sources[:10])
        matches = "\n".join(
            f"- {s['entity_type']}: {s.get('safe_label', s['canonical_id'])}" for s in semantic[:8]
        )
        facts = "\n".join(HybridKnowledgeRetriever._natural_fact(r) for r in relations[:12])
        semantic_facts = []
        for row in approved_semantics or []:
            semantic_facts.append(
                f'- Propósito aprobado de la pantalla "{row["safe_label"]}": {row["purpose_summary"]}'
            )
            semantic_facts.extend(
                f'- Capacidad aprobada de "{row["safe_label"]}": {statement}'
                for statement in row.get("supported_capabilities", [])
            )
        approved = "\n".join(semantic_facts[:12])
        return (
            f"COINCIDENCIAS SEMÁNTICAS ESTRUCTURALES\n{matches}\n\n"
            f"SEMÁNTICA HUMANA APROBADA\n{approved}\n\n"
            f"ENTIDADES VALIDADAS\n{entities}\n\nRELACIONES VALIDADAS\n{facts}"
        )[:6000]

    @staticmethod
    def _natural_fact(r):
        templates = {
            "HAS_MODULE": 'El ERP "{s}" contiene el módulo "{t}".',
            "HAS_SCREEN": 'El {st} "{s}" contiene la pantalla "{t}".',
            "HAS_FIELD": 'La pantalla "{s}" contiene el campo "{t}".',
            "HAS_CONTROL": 'La pantalla "{s}" contiene el control "{t}".',
            "HAS_TABLE": 'La pantalla "{s}" contiene la tabla "{t}".',
            "HAS_COLUMN": 'La tabla "{s}" contiene la columna "{t}".',
        }
        template = templates.get(r["relationship_type"])
        return (
            template.format(
                s=r["source_label"],
                t=r["target_label"],
                st=TYPE_NAMES.get(r["source_type"], r["source_type"]),
            )
            if template
            else f'"{r["source_label"]}" se relaciona mediante {r["relationship_type"]} '
            f'con "{r["target_label"]}".'
        )

    @staticmethod
    def _needs_abstention(question, result):
        terms = {
            action
            for action, pattern in MUTATIVE_FORMS.items()
            if any(
                re.fullmatch(pattern, token)
                for token in re.findall(r"[\wáéíóúñ]+", question.casefold())
            )
        }
        if not terms:
            return False
        evidence = [
            s for s in result["sources"] if s["entity_type"] in {"control", "event", "transition"}
        ]
        equivalents = {
            "crear": ("crear", "nuevo", "nueva", "agregar", "añadir", "registrar"),
            "eliminar": ("eliminar", "borrar", "remover"),
            "guardar": ("guardar", "confirmar", "aceptar", "aplicar"),
        }
        return not any(
            any(word in e["safe_label"].casefold() for word in equivalents.get(term, (term,)))
            for term in terms
            for e in evidence
        )
