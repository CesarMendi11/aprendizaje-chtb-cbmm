from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import httpx

from src.api.app import create_app
from src.hybrid.conversation_store import ConversationStateStore
from src.hybrid.factory import HybridRetrieverFactory


@dataclass(frozen=True)
class SourceExpectation:
    title: str
    source_type: str


@dataclass(frozen=True)
class TurnExpectation:
    intent: str | None = None
    decision: str | None = None
    reason: str | None = None
    status: str | None = None
    answer_contains: tuple[str, ...] = ()
    answer_excludes: tuple[str, ...] = ()
    required_sources: tuple[SourceExpectation, ...] = ()
    forbidden_source_titles: tuple[str, ...] = ()
    allowed_source_types: tuple[str, ...] = ()
    retrieval_exact: tuple[tuple[str, int], ...] = ()
    retrieval_zero: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnCase:
    name: str
    question: str
    expectation: TurnExpectation


@dataclass(frozen=True)
class Scenario:
    name: str
    turns: tuple[TurnCase, ...]


MANDATORY_RESPONSE_KEYS = {
    "answer",
    "conversationId",
    "status",
    "sources",
    "answerDecision",
    "answer_mode",
    "intent",
    "confidence",
    "evidence_ids",
    "retrieval",
}

CANONICAL_ID_PREFIXES = (
    "screen:",
    "module:",
    "field:",
    "control:",
    "table:",
    "table_column:",
    "event:",
    "transition:",
    "ui_state:",
    "erp:",
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX_PATH = ROOT / "configs" / "m3_9_conversational_matrix.json"


def load_matrix(path: Path | str) -> tuple[Scenario, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    scenarios: list[Scenario] = []

    for scenario_data in raw.get("scenarios", []):
        turns: list[TurnCase] = []
        for turn_data in scenario_data.get("turns", []):
            expectation_data = dict(turn_data.get("expectation") or {})
            required_sources = tuple(
                SourceExpectation(
                    title=str(source["title"]),
                    source_type=str(source["source_type"]),
                )
                for source in expectation_data.pop("required_sources", [])
            )
            retrieval_exact = tuple(
                (str(item[0]), int(item[1]))
                for item in expectation_data.pop("retrieval_exact", [])
            )
            expectation = TurnExpectation(
                intent=expectation_data.get("intent"),
                decision=expectation_data.get("decision"),
                reason=expectation_data.get("reason"),
                status=expectation_data.get("status"),
                answer_contains=tuple(expectation_data.get("answer_contains") or ()),
                answer_excludes=tuple(expectation_data.get("answer_excludes") or ()),
                required_sources=required_sources,
                forbidden_source_titles=tuple(
                    expectation_data.get("forbidden_source_titles") or ()
                ),
                allowed_source_types=tuple(
                    expectation_data.get("allowed_source_types") or ()
                ),
                retrieval_exact=retrieval_exact,
                retrieval_zero=tuple(expectation_data.get("retrieval_zero") or ()),
            )
            turns.append(
                TurnCase(
                    name=str(turn_data["name"]),
                    question=str(turn_data["question"]),
                    expectation=expectation,
                )
            )
        scenarios.append(
            Scenario(
                name=str(scenario_data["name"]),
                turns=tuple(turns),
            )
        )

    return tuple(scenarios)


def default_matrix() -> tuple[Scenario, ...]:
    return load_matrix(DEFAULT_MATRIX_PATH)


def evaluate_payload(payload: dict[str, object], expectation: TurnExpectation) -> list[str]:
    errors: list[str] = []
    missing = sorted(MANDATORY_RESPONSE_KEYS - set(payload))
    if missing:
        errors.append(f"missing response keys: {', '.join(missing)}")

    decision = payload.get("answerDecision")
    if not isinstance(decision, dict):
        decision = {}
        errors.append("answerDecision must be an object")

    if expectation.intent is not None and payload.get("intent") != expectation.intent:
        errors.append(
            f"intent expected {expectation.intent!r}, got {payload.get('intent')!r}"
        )
    if expectation.decision is not None and decision.get("decision") != expectation.decision:
        errors.append(
            f"decision expected {expectation.decision!r}, got {decision.get('decision')!r}"
        )
    if expectation.reason is not None and decision.get("reason") != expectation.reason:
        errors.append(
            f"reason expected {expectation.reason!r}, got {decision.get('reason')!r}"
        )
    if expectation.status is not None and payload.get("status") != expectation.status:
        errors.append(
            f"status expected {expectation.status!r}, got {payload.get('status')!r}"
        )

    answer = str(payload.get("answer") or "")
    answer_folded = answer.casefold()
    for text in expectation.answer_contains:
        if text.casefold() not in answer_folded:
            errors.append(f"answer must contain {text!r}")
    for text in expectation.answer_excludes:
        if text.casefold() in answer_folded:
            errors.append(f"answer must not contain {text!r}")
    for prefix in CANONICAL_ID_PREFIXES:
        if prefix in answer_folded:
            errors.append(f"answer leaked canonical id prefix {prefix!r}")

    raw_sources = payload.get("sources")
    sources = raw_sources if isinstance(raw_sources, list) else []
    source_pairs = {
        (str(row.get("title") or ""), str(row.get("sourceType") or ""))
        for row in sources
        if isinstance(row, dict)
    }
    source_titles = {title for title, _ in source_pairs}
    source_types = {source_type for _, source_type in source_pairs}

    for required in expectation.required_sources:
        if (required.title, required.source_type) not in source_pairs:
            errors.append(
                "missing source "
                f"{required.title!r} with type {required.source_type!r}"
            )
    for title in expectation.forbidden_source_titles:
        if title in source_titles:
            errors.append(f"forbidden source title present: {title!r}")
    if expectation.allowed_source_types:
        invalid_types = sorted(source_types - set(expectation.allowed_source_types))
        if invalid_types:
            errors.append(
                f"unexpected source types: {', '.join(invalid_types)}"
            )

    retrieval = payload.get("retrieval")
    retrieval = retrieval if isinstance(retrieval, dict) else {}
    for key, expected in expectation.retrieval_exact:
        if retrieval.get(key) != expected:
            errors.append(
                f"retrieval.{key} expected {expected!r}, got {retrieval.get(key)!r}"
            )
    for key in expectation.retrieval_zero:
        if retrieval.get(key) != 0:
            errors.append(
                f"retrieval.{key} expected 0, got {retrieval.get(key)!r}"
            )

    return errors


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


async def run_matrix(
    scenarios: Iterable[Scenario],
    *,
    output: Path | None = None,
) -> dict[str, object]:
    store = ConversationStateStore(max_entries=128, ttl_seconds=3600)
    app = create_app(conversation_state_store=store)
    app.state.hybrid_factory = HybridRetrieverFactory()
    transport = httpx.ASGITransport(app=app)

    rows: list[dict[str, object]] = []
    latencies: list[float] = []

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://m3-9.local",
        timeout=120.0,
    ) as client:
        for scenario in scenarios:
            conversation_id: str | None = None
            for index, turn in enumerate(scenario.turns, start=1):
                request_payload: dict[str, object] = {"question": turn.question}
                if conversation_id:
                    request_payload["conversationId"] = conversation_id

                started = time.perf_counter()
                response = await client.post("/api/chat", json=request_payload)
                elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
                latencies.append(elapsed_ms)

                payload: dict[str, object]
                try:
                    payload = response.json()
                except Exception:
                    payload = {}

                errors: list[str] = []
                if response.status_code != 200:
                    errors.append(f"HTTP status expected 200, got {response.status_code}")
                else:
                    errors.extend(evaluate_payload(payload, turn.expectation))

                returned_conversation_id = str(payload.get("conversationId") or "").strip()
                if not returned_conversation_id:
                    errors.append("conversationId missing or blank")
                elif conversation_id and returned_conversation_id != conversation_id:
                    errors.append(
                        "conversationId changed within scenario: "
                        f"{conversation_id!r} -> {returned_conversation_id!r}"
                    )
                elif not conversation_id:
                    conversation_id = returned_conversation_id

                decision = payload.get("answerDecision")
                decision = decision if isinstance(decision, dict) else {}
                sources = payload.get("sources")
                sources = sources if isinstance(sources, list) else []
                retrieval = payload.get("retrieval")
                retrieval = retrieval if isinstance(retrieval, dict) else {}

                row = {
                    "scenario": scenario.name,
                    "turn": index,
                    "case": turn.name,
                    "question": turn.question,
                    "passed": not errors,
                    "errors": errors,
                    "latency_ms": elapsed_ms,
                    "conversation_id": returned_conversation_id,
                    "status": payload.get("status"),
                    "intent": payload.get("intent"),
                    "decision": decision.get("decision"),
                    "reason": decision.get("reason"),
                    "confidence": payload.get("confidence"),
                    "sources": sources,
                    "retrieval": retrieval,
                    "answer": payload.get("answer"),
                }
                rows.append(row)

                marker = "PASS" if not errors else "FAIL"
                print(
                    f"{marker:4} | {scenario.name:<36} | {turn.name:<34} "
                    f"| {elapsed_ms:8.2f} ms | {payload.get('intent')} "
                    f"| {decision.get('decision')}"
                )
                for error in errors:
                    print(f"       - {error}")

    passed = sum(1 for row in rows if row["passed"])
    failed = len(rows) - passed
    summary = {
        "turns": len(rows),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / len(rows), 4) if rows else 0.0,
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "p50": round(_percentile(latencies, 0.50), 2),
            "p95": round(_percentile(latencies, 0.95), 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
    }
    report = {
        "matrix": "M3.9 conversational API baseline",
        "summary": summary,
        "rows": rows,
    }

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"REPORT: {output}")

    print()
    print(
        "M3_9_MATRIX_SUMMARY: "
        f"turns={summary['turns']} passed={summary['passed']} "
        f"failed={summary['failed']} p50_ms={summary['latency_ms']['p50']} "
        f"p95_ms={summary['latency_ms']['p95']}"
    )
    if failed == 0:
        print("M3_9_CONVERSATIONAL_MATRIX_CERTIFIED: OK")
    else:
        print("M3_9_CONVERSATIONAL_MATRIX_CERTIFIED: FAIL")

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the real M3.9 /api/chat conversational quality matrix."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON report path.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="Run only the named scenario (repeatable).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenarios = default_matrix()
    if args.scenario:
        requested = set(args.scenario)
        known = {scenario.name for scenario in scenarios}
        unknown = sorted(requested - known)
        if unknown:
            raise SystemExit(f"Unknown scenario(s): {', '.join(unknown)}")
        scenarios = tuple(s for s in scenarios if s.name in requested)

    report = asyncio.run(run_matrix(scenarios, output=args.output))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
