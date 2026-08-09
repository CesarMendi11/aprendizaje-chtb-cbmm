from __future__ import annotations

import re
from collections import OrderedDict

from src.database.services import ChromaSyncService
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
        self, session, *, chroma, neo4j, embeddings, generator=None, planner=None, aliases=None
    ):
        self.session, self.chroma, self.neo4j = session, chroma, neo4j
        self.embeddings, self.generator = embeddings, generator
        self.planner = planner or StructuralAnswerPlanner(aliases)

    def retrieve(
        self, question, *, erp_id=None, knowledge_version=None, semantic_top_k=8, graph_limit=20
    ):
        version, _, _ = ChromaSyncService(self.session).prepare(
            erp_id=erp_id, knowledge_version=knowledge_version
        )
        erp_id, knowledge_version = version.erp_id, version.knowledge_version
        semantic = self.chroma.query(
            self.embeddings.embed(question)[0],
            top_k=semantic_top_k,
            erp_id=erp_id,
            knowledge_version=knowledge_version,
        )
        seeds = [row["canonical_id"] for row in semantic]
        neighbors = self._expand(seeds, erp_id, knowledge_version, graph_limit)
        ids = list(OrderedDict.fromkeys(seeds + [n["canonical_id"] for n in neighbors]))
        valid = {i.canonical_id: i for i in self._validate(ids, version.id)}
        semantic_by_id = {row["canonical_id"]: row for row in semantic}
        graph_ids = {n["canonical_id"] for n in neighbors}
        sources = []
        for cid in ids:
            item = valid.get(cid)
            if not item:
                continue
            hit = semantic_by_id.get(cid)
            payload = self._effective(item.id)
            sources.append(
                {
                    "canonical_id": cid,
                    "entity_type": item.entity_type,
                    "safe_label": self._label(item.entity_type, payload),
                    "screen_route": item.route,
                    "origin": "semantic+graph"
                    if hit and cid in graph_ids
                    else ("semantic" if hit else "graph"),
                    "score": hit.get("score") if hit else None,
                }
            )
        relations = self._relations(neighbors, valid)
        route_by_screen = {
            s["canonical_id"]: s.get("screen_route")
            for s in sources
            if s["entity_type"] == "screen" and s.get("screen_route")
        }
        for source in sources:
            if source.get("screen_route"):
                continue
            relation = next(
                (
                    r
                    for r in relations
                    if r["target_canonical_id"] == source["canonical_id"]
                    and r["relationship_type"] in {"HAS_FIELD", "HAS_CONTROL", "HAS_TABLE"}
                ),
                None,
            )
            if relation:
                source["screen_route"] = route_by_screen.get(relation["source_canonical_id"])
        return {
            "status": "ok",
            "question": question,
            "erp_id": erp_id,
            "knowledge_version": knowledge_version,
            "retrieval": {
                "semantic_hits": len(semantic),
                "graph_neighbors": len(neighbors),
                "validated_items": len(sources),
            },
            "sources": sources[:10],
            "relations": relations,
            "context": self._context(sources, relations, semantic),
        }

    def ask(self, question, *, generate=True, **kwargs):
        result = self.retrieve(question, **kwargs)
        plan = self.planner.plan(
            question, result["sources"], result.get("relations", []), result["sources"]
        )
        result["intent"] = plan.get("intent")
        result["confidence"] = plan.get("confidence")
        result["evidence_ids"] = plan.get("evidence_ids", [])
        result["answer_mode"] = "insufficient_evidence"
        if plan["supported"]:
            result["answer"] = plan["answer"]
            result["answer_mode"] = "deterministic_graph"
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
            result["answer"] = self.generator.generate(prompt, system=SYSTEM_PROMPT)
        if generate:
            result.pop("context", None)
        return result

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
        repo = ChromaSyncService(self.session).knowledge
        return [
            item
            for item in repo.list_items(version_id=version_id, limit=1000)
            if item.canonical_id in ids
            and str(item.current_review_status) in {"approved", "corrected"}
        ]

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
    def _context(sources, relations, semantic):
        entities = "\n".join(f"- {s['entity_type']}: {s['safe_label']}" for s in sources[:10])
        matches = "\n".join(
            f"- {s['entity_type']}: {s.get('safe_label', s['canonical_id'])}" for s in semantic[:8]
        )
        facts = "\n".join(HybridKnowledgeRetriever._natural_fact(r) for r in relations[:12])
        return (
            f"COINCIDENCIAS SEMÁNTICAS\n{matches}\n\nENTIDADES VALIDADAS\n{entities}\n\n"
            f"RELACIONES VALIDADAS\n{facts}"
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
