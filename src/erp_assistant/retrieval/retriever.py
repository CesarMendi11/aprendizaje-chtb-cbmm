from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import replace

from sqlalchemy import select

from erp_assistant.persistence.postgres.models import KnowledgeItem
from erp_assistant.projections.chroma.structural_sync_service import ChromaSyncService
from erp_assistant.semantic.services.semantic_retrieval_authorization_service import (
    SemanticRetrievalAuthorizationService,
)
from erp_assistant.structural.canonical.enums import ReviewStatus
from erp_assistant.structural.canonical.privacy import sanitize_text
from erp_assistant.structural.services.effective_knowledge_service import EffectiveKnowledgeService

from .answer_decision import (
    AnswerDecisionPlanner,
    AnswerDecisionType,
    render_clarification,
)
from .answer_planner import StructuralAnswerPlanner
from .context_builder import EvidenceContextBuilder
from .conversation_context import (
    ConversationContextMode,
    ConversationContextResolver,
    ConversationState,
    render_missing_context_clarification,
)
from .entity_resolver import CanonicalEntityResolver, EntityResolution
from .evidence_selector import EvidenceSelection, EvidenceSelector
from .graph_expansion import QueryAwareGraphExpansionPlanner
from .query_plan import QueryPlan, QueryPlanner
from .rank_fusion import RankedItem, ReciprocalRankFusion

ALLOWED_RELATIONSHIPS = {
    "HAS_MODULE",
    "HAS_SUBMODULE",
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
        answer_decision_planner=None,
        query_planner=None,
        entity_resolver=None,
        rank_fusion=None,
        graph_planner=None,
        evidence_selector=None,
        context_builder=None,
        conversation_context_resolver=None,
        aliases=None,
    ):
        self.session, self.chroma, self.neo4j = session, chroma, neo4j
        self.semantic_chroma = semantic_chroma
        self.effective = EffectiveKnowledgeService(session) if session is not None else None
        self._effective_cache = {}
        self._effective_items = {}
        self.embeddings, self.generator = embeddings, generator
        self.semantic_authorizer = semantic_authorizer or (
            SemanticRetrievalAuthorizationService(session) if session is not None else None
        )
        self.query_planner = query_planner or QueryPlanner()
        self.rank_fusion = rank_fusion or ReciprocalRankFusion()
        self.graph_planner = graph_planner or QueryAwareGraphExpansionPlanner()
        self.evidence_selector = evidence_selector or EvidenceSelector()
        self.context_builder = context_builder or EvidenceContextBuilder()
        self.conversation_context_resolver = (
            conversation_context_resolver
            or ConversationContextResolver(query_planner=self.query_planner)
        )
        self.entity_resolver = entity_resolver
        if self.entity_resolver is None and session is not None and hasattr(session, "execute"):
            self.entity_resolver = CanonicalEntityResolver(session, aliases=aliases)
        self.planner = planner or StructuralAnswerPlanner(aliases, query_planner=self.query_planner)
        self.answer_decision_planner = answer_decision_planner or AnswerDecisionPlanner()

    def retrieve(
        self,
        question,
        *,
        erp_id=None,
        knowledge_version=None,
        semantic_top_k=8,
        entity_top_k=12,
        graph_limit=20,
        query_plan: QueryPlan | None = None,
        conversation_state: ConversationState | dict[str, object] | None = None,
    ):
        self._effective_cache = {}
        self._effective_items = {}
        query_plan = query_plan or self.query_planner.plan(question)
        version = ChromaSyncService(self.session).resolve_version(
            erp_id=erp_id, knowledge_version=knowledge_version
        )
        erp_id, knowledge_version = version.erp_id, version.knowledge_version
        resolution = (
            self.entity_resolver.resolve(
                query_plan,
                version_id=version.id,
                limit=entity_top_k,
            )
            if self.entity_resolver is not None
            else EntityResolution(
                query=question,
                normalized_query=query_plan.normalized_question,
                candidates=(),
            )
        )
        conversation_context = self.conversation_context_resolver.resolve(
            question,
            conversation_state,
            query_plan=query_plan,
            direct_resolution=resolution,
            erp_id=erp_id,
            knowledge_version=knowledge_version,
        )
        effective_question = conversation_context.effective_question

        if conversation_context.mode == ConversationContextMode.CLARIFICATION_REQUIRED:
            evidence = EvidenceSelection(
                status="clarification_required",
                reason=conversation_context.reason,
                focal_canonical_ids=(),
                sources=(),
                relations=(),
                approved_semantics=(),
                clarification_candidates=(),
            )
            return {
                "status": "ok",
                "question": question,
                "effective_question": effective_question,
                "query_plan": query_plan.as_dict(),
                "erp_id": erp_id,
                "knowledge_version": knowledge_version,
                "conversation_context": conversation_context.as_dict(),
                "entity_resolution": resolution.as_dict(),
                "retrieval": {
                    "entity_candidates": len(resolution.candidates),
                    "semantic_hits": 0,
                    "structural_dense_hits": 0,
                    "semantic_candidates": 0,
                    "approved_semantic_hits": 0,
                    "semantic_dense_hits": 0,
                    "graph_neighbors": 0,
                    "graph_seed_count": 0,
                    "validated_items": 0,
                    "selected_sources": 0,
                    "selected_relations": 0,
                    "selected_semantics": 0,
                },
                "graph_expansion": {
                    "enabled": False,
                    "strategy": "conversation_context",
                    "reason": conversation_context.reason,
                    "seed_canonical_ids": [],
                    "seed_entity_types": [],
                    "endpoint_entity_types": [],
                    "relationships": [],
                    "max_hops": 0,
                    "limit": 0,
                },
                "rank_fusion": {
                    "algorithm": "rrf",
                    "k": self.rank_fusion.k,
                    "channel_sizes": {
                        "canonical": 0,
                        "lexical": 0,
                        "trigram": 0,
                        "structural_dense": 0,
                        "semantic_dense": 0,
                    },
                    "excluded_ambiguous_canonical_ids": [],
                    "candidates": [],
                },
                "evidence_selection": evidence.as_dict(),
                "sources": [],
                "relations": [],
                "approved_semantics": [],
                "context": "",
            }

        if (
            conversation_context.mode == ConversationContextMode.DIRECT
            and resolution.status == "ambiguous"
            and not query_plan.mutative_action
            and "screen" in query_plan.target_entity_types
            and self.entity_resolver is not None
            and hasattr(self.entity_resolver, "resolve_in_screen")
        ):
            screen_plan = replace(
                query_plan,
                target_entity_types=("screen",),
            )
            screen_resolution = self.entity_resolver.resolve(
                screen_plan,
                version_id=version.id,
                limit=entity_top_k,
            )
            screen_id = screen_resolution.primary_canonical_id
            screen_candidate = next(
                (
                    candidate
                    for candidate in screen_resolution.candidates
                    if candidate.canonical_id == screen_id
                ),
                None,
            )
            explicit_channels = {"normalized_mention", "alias", "normalized_containment"}
            if (
                screen_id
                and screen_candidate is not None
                and explicit_channels.intersection(screen_candidate.channels)
            ):
                scoped_resolution = self.entity_resolver.resolve_in_screen(
                    query_plan,
                    version_id=version.id,
                    screen_id=screen_id,
                    limit=entity_top_k,
                )
                if scoped_resolution.candidates:
                    resolution = scoped_resolution
                    conversation_context = replace(
                        conversation_context,
                        reason="current_turn_screen_scope",
                    )

        if conversation_context.mode == ConversationContextMode.CONTEXTUALIZED:
            query_plan = self.query_planner.plan(effective_question)
            resolution = (
                self.entity_resolver.resolve(
                    query_plan,
                    version_id=version.id,
                    limit=entity_top_k,
                )
                if self.entity_resolver is not None
                else EntityResolution(
                    query=effective_question,
                    normalized_query=query_plan.normalized_question,
                    candidates=(),
                )
            )
            inherited_screen = next(
                (
                    row
                    for row in conversation_context.inherited_entities
                    if row.entity_type == "screen"
                ),
                None,
            )
            if (
                inherited_screen is not None
                and self.entity_resolver is not None
                and hasattr(self.entity_resolver, "scope_to_screen")
            ):
                resolution = self.entity_resolver.scope_to_screen(
                    resolution,
                    version_id=version.id,
                    screen_id=inherited_screen.canonical_id,
                    context_label=inherited_screen.safe_label,
                )

        # Unknown natural-language questions may still use the generic grounded
        # path when they contain a strong canonical ERP entity (for example
        # "Cuéntame sobre Año").  Without such an anchor, fail closed before
        # dense retrieval so unrelated vector neighbors cannot manufacture an
        # apparently grounded context for general-knowledge questions.
        if (
            query_plan.intent is None
            and resolution.status != "ambiguous"
            and not resolution.seed_candidates
        ):
            evidence = EvidenceSelection(
                status="insufficient",
                reason="insufficient_evidence",
                focal_canonical_ids=(),
                sources=(),
                relations=(),
                approved_semantics=(),
            )
            return {
                "status": "ok",
                "question": question,
                "effective_question": effective_question,
                "query_plan": query_plan.as_dict(),
                "erp_id": erp_id,
                "knowledge_version": knowledge_version,
                "conversation_context": conversation_context.as_dict(),
                "entity_resolution": resolution.as_dict(),
                "retrieval": {
                    "entity_candidates": len(resolution.candidates),
                    "semantic_hits": 0,
                    "structural_dense_hits": 0,
                    "semantic_candidates": 0,
                    "approved_semantic_hits": 0,
                    "semantic_dense_hits": 0,
                    "graph_neighbors": 0,
                    "graph_seed_count": 0,
                    "validated_items": 0,
                    "selected_sources": 0,
                    "selected_relations": 0,
                    "selected_semantics": 0,
                },
                "graph_expansion": {
                    "enabled": False,
                    "strategy": "unsupported_query",
                    "reason": "insufficient_evidence",
                    "seed_canonical_ids": [],
                    "seed_entity_types": [],
                    "endpoint_entity_types": [],
                    "relationships": [],
                    "max_hops": 0,
                    "limit": 0,
                },
                "rank_fusion": {
                    "algorithm": "rrf",
                    "k": self.rank_fusion.k,
                    "channel_sizes": {
                        "canonical": 0,
                        "lexical": 0,
                        "trigram": 0,
                        "structural_dense": 0,
                        "semantic_dense": 0,
                    },
                    "excluded_ambiguous_canonical_ids": [],
                    "candidates": [],
                },
                "evidence_selection": evidence.as_dict(),
                "sources": [],
                "relations": [],
                "approved_semantics": [],
                "context": "",
            }

        query_embedding = self.embeddings.embed(effective_question)[0]
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

        rankings = {
            "canonical": [
                RankedItem(canonical_id, score)
                for canonical_id, score in resolution.ranking("canonical")
            ],
            "lexical": [
                RankedItem(canonical_id, score)
                for canonical_id, score in resolution.ranking("lexical")
            ],
            "trigram": [
                RankedItem(canonical_id, score)
                for canonical_id, score in resolution.ranking("trigram")
            ],
            "structural_dense": [
                RankedItem(row["canonical_id"], row.get("score"))
                for row in semantic
                if row.get("canonical_id")
            ],
            "semantic_dense": [
                RankedItem(row["screen_id"], row.get("score"))
                for row in approved_semantics
                if row.get("screen_id")
            ],
        }
        fused = self.rank_fusion.fuse(rankings)
        ambiguous_ids = set(resolution.ambiguous_candidate_ids)

        # RRF ranking is retrieval, not authority. Validate candidates in
        # PostgreSQL before any of them are allowed to become Neo4j seeds.
        fused_ids = [
            candidate.canonical_id
            for candidate in fused
            if candidate.canonical_id not in ambiguous_ids
        ]
        prevalidated = {item.canonical_id: item for item in self._validate(fused_ids, version.id)}
        candidate_types = {
            canonical_id: item.entity_type for canonical_id, item in prevalidated.items()
        }
        graph_plan = self.graph_planner.plan(
            query_plan,
            resolution,
            fused,
            candidate_types=candidate_types,
            graph_limit=graph_limit,
        )
        neighbors = (
            self._expand(
                list(graph_plan.seed_canonical_ids),
                erp_id,
                knowledge_version,
                graph_plan.limit,
                relationships=graph_plan.relationships,
                endpoint_entity_types=graph_plan.endpoint_entity_types,
                max_hops=graph_plan.max_hops,
            )
            if graph_plan.enabled
            else []
        )
        ids = self._candidate_ids(fused_ids, neighbors)
        valid = {i.canonical_id: i for i in self._validate(ids, version.id)}
        self._effective_items = {item.id: item for item in valid.values()}
        semantic_by_id = {row["canonical_id"]: row for row in semantic}
        approved_semantics_by_screen = {row["screen_id"]: row for row in approved_semantics}
        resolved_by_id = {candidate.canonical_id: candidate for candidate in resolution.candidates}
        fused_by_id = {candidate.canonical_id: candidate for candidate in fused}
        fused_rank_by_id = {
            candidate.canonical_id: rank for rank, candidate in enumerate(fused, start=1)
        }
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
            resolved_candidate = resolved_by_id.get(cid)
            fused_candidate = fused_by_id.get(cid)
            sources.append(
                {
                    "canonical_id": cid,
                    "entity_type": item.entity_type,
                    "safe_label": self._label(item.entity_type, payload),
                    "screen_route": item.route,
                    "origin": origin,
                    "score": (
                        resolved_candidate.score
                        if resolved_candidate is not None
                        else approved_semantic.get("score")
                        if approved_semantic is not None
                        else (hit.get("score") if hit else None)
                    ),
                    "resolution_channels": (
                        list(resolved_candidate.channels) if resolved_candidate is not None else []
                    ),
                    "retrieval_rank": fused_rank_by_id.get(cid),
                    "rrf_score": (
                        round(float(fused_candidate.rrf_score), 9)
                        if fused_candidate is not None
                        else None
                    ),
                    "retrieval_channels": (
                        list(fused_candidate.channels)
                        if fused_candidate is not None
                        else ["graph"]
                        if cid in graph_ids
                        else []
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

        evidence = self.evidence_selector.select(
            query_plan,
            resolution,
            graph_plan,
            sources,
            relations,
            approved_semantics,
        )
        selected_sources = list(evidence.sources)
        selected_relations = list(evidence.relations)
        selected_semantics = list(evidence.approved_semantics)
        context = self.context_builder.build(query_plan, evidence)

        return {
            "status": "ok",
            "question": question,
            "effective_question": effective_question,
            "query_plan": query_plan.as_dict(),
            "erp_id": erp_id,
            "conversation_context": conversation_context.as_dict(),
            "knowledge_version": knowledge_version,
            "entity_resolution": resolution.as_dict(),
            "retrieval": {
                "entity_candidates": len(resolution.candidates),
                "semantic_hits": len(semantic),
                "structural_dense_hits": len(semantic),
                "semantic_candidates": len(semantic_candidates),
                "approved_semantic_hits": len(approved_semantics),
                "semantic_dense_hits": len(approved_semantics),
                "graph_neighbors": len(neighbors),
                "graph_seed_count": len(graph_plan.seed_canonical_ids),
                "validated_items": len(sources),
                "selected_sources": len(selected_sources),
                "selected_relations": len(selected_relations),
                "selected_semantics": len(selected_semantics),
            },
            "graph_expansion": graph_plan.as_dict(),
            "rank_fusion": {
                "algorithm": "rrf",
                "k": self.rank_fusion.k,
                "channel_sizes": {channel: len(rows) for channel, rows in rankings.items()},
                "excluded_ambiguous_canonical_ids": sorted(ambiguous_ids),
                "candidates": [candidate.as_dict() for candidate in fused],
            },
            "evidence_selection": evidence.as_dict(),
            "sources": selected_sources,
            "relations": selected_relations,
            "approved_semantics": selected_semantics,
            "context": context,
        }

    def ask(self, question, *, generate=True, conversation_state=None, **kwargs):
        previous_state = ConversationState.coerce(conversation_state)
        initial_query_plan = self.query_planner.plan(question)
        result = self.retrieve(
            question,
            query_plan=initial_query_plan,
            conversation_state=previous_state,
            **kwargs,
        )
        effective_question = result.get("effective_question") or question
        query_plan = (
            initial_query_plan
            if effective_question == question
            else self.query_planner.plan(effective_question)
        )
        result.setdefault("query_plan", query_plan.as_dict())

        structural_plan = self.planner.plan(
            effective_question,
            result.get("sources", []),
            result.get("relations", []),
            result.get("sources", []),
            approved_semantics=result.get("approved_semantics", []),
            query_plan=query_plan,
        )
        decision = self.answer_decision_planner.decide(
            query_plan,
            evidence_selection=result.get("evidence_selection"),
            deterministic_plan=structural_plan,
            has_context=bool(result.get("context")),
            has_sources=bool(result.get("sources")),
            policy_abstention=self._needs_abstention(question, result),
        )

        result["intent"] = structural_plan.get("intent")
        result["confidence"] = decision.confidence
        result["evidence_ids"] = structural_plan.get("evidence_ids", [])
        result["answer_decision"] = decision.as_dict()

        def finalize():
            selection = result.get("evidence_selection") or {}
            next_state = self.conversation_context_resolver.next_state(
                previous_state,
                erp_id=str(result.get("erp_id") or ""),
                knowledge_version=str(result.get("knowledge_version") or ""),
                query_plan=query_plan,
                answer_decision=str(result.get("answer_decision", {}).get("decision") or ""),
                sources=result.get("sources", []),
                clarification_candidates=selection.get("clarification_candidates", []),
                evidence_ids=result.get("evidence_ids", []),
            )
            result["conversation_state"] = next_state.as_dict()
            return result

        if decision.decision == AnswerDecisionType.CLARIFICATION:
            candidates = result.get("evidence_selection", {}).get("clarification_candidates", [])
            reason = str(result.get("answer_decision", {}).get("reason") or "")
            result["answer"] = (
                render_missing_context_clarification(reason)
                if reason.startswith("conversation_")
                else render_clarification(candidates)
            )
            result["answer_mode"] = "clarification"
            result["evidence_ids"] = []
            result.pop("context", None)
            return finalize()

        if decision.decision == AnswerDecisionType.DETERMINISTIC_ANSWER:
            result["answer"] = structural_plan["answer"]
            result["answer_mode"] = structural_plan.get("answer_mode", "deterministic_graph")
            result["evidence_ids"] = structural_plan.get("evidence_ids", [])
            result.pop("context", None)
            return finalize()

        if decision.decision == AnswerDecisionType.ABSTENTION:
            result["answer"] = ABSTAIN
            result["answer_mode"] = (
                "policy_abstention"
                if decision.reason == "mutative_action_policy"
                else "insufficient_evidence"
            )
            result["evidence_ids"] = []
            result.pop("context", None)
            return finalize()

        # GROUNDED_LLM is the only path on which generated prose is allowed.
        result["answer_mode"] = "ollama_grounded"
        if not generate or not self.generator:
            result["answer"] = None
            return finalize()

        prompt = (
            f"Pregunta contextualizada:\n{effective_question}\n\nContexto validado:\n"
            f"{result['context']}\n\nResponde únicamente con información respaldada "
            f"explícitamente por el contexto. Puedes interpretar abreviaturas y sinónimos "
            f"comunes, pero no inventes estructura ni procedimientos. Si no basta, responde "
            f"exactamente:\n{ABSTAIN}"
        )
        generated_answer = self.generator.generate(prompt, system=SYSTEM_PROMPT)
        result["answer"] = generated_answer
        if self._is_abstention(generated_answer):
            final_decision = self.answer_decision_planner.generator_abstention(query_plan)
            result["answer_decision"] = final_decision.as_dict()
            result["confidence"] = final_decision.confidence
            result["answer_mode"] = "insufficient_evidence"
            result["evidence_ids"] = []
        result.pop("context", None)
        return finalize()

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
        endpoint_ids = [row.get("canonical_id") for row in neighbors if row.get("canonical_id")]
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
        return list(OrderedDict.fromkeys(list(seeds) + endpoint_ids + path_ids))

    def _expand(
        self,
        seeds,
        erp_id,
        version,
        limit,
        *,
        relationships=None,
        endpoint_entity_types=(),
        max_hops=2,
    ):
        if not seeds:
            return []
        relationships = tuple(relationships or sorted(ALLOWED_RELATIONSHIPS))
        endpoint_entity_types = tuple(endpoint_entity_types or ())
        max_hops = max(1, min(3, int(max_hops)))
        query = (
            "MATCH p=(a)-[*1..3]-(b) WHERE a.canonical_id IN $seeds "
            "AND a.erp_id=$erp_id AND a.knowledge_version=$version "
            "AND b.canonical_id <> a.canonical_id AND b.erp_id=$erp_id "
            "AND b.knowledge_version=$version "
            "AND length(p) <= $max_hops "
            "AND all(rel IN relationships(p) WHERE type(rel) IN $rels) "
            "AND (size($endpoint_types) = 0 OR b.entity_type IN $endpoint_types) "
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
                "rels": sorted(relationships),
                "endpoint_types": list(endpoint_entity_types),
                "max_hops": max_hops,
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
        cached = self._effective_cache.get(item_id)
        if cached is not None:
            return cached
        if self._effective_items and self.effective is not None:
            descriptions = self.effective.describe_many(self._effective_items.values())
            self._effective_cache.update(
                {
                    current_id: description["effective_payload"]
                    for current_id, description in descriptions.items()
                }
            )
            cached = self._effective_cache.get(item_id)
            if cached is not None:
                return cached
        payload = ChromaSyncService(self.session).effective.describe(item_id)["effective_payload"]
        self._effective_cache[item_id] = payload
        return payload

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
            s
            for s in result.get("sources", [])
            if s.get("entity_type") in {"control", "event", "transition"}
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
