from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from erp_assistant.api.app import create_app
from erp_assistant.config.api_settings import ApiSettings
from erp_assistant.config.chroma_settings import ChromaSettings
from erp_assistant.config.database_settings import DatabaseSettings
from erp_assistant.retrieval.conversation_store import ConversationStateStore
from scripts.experiments.common import utc_now_iso, write_json_atomic
from scripts.experiments.rq3_harness import CaptureRecord

EXPECTED_QUERIES = 54
EXPECTED_RUNS = 216
EXPECTED_VARIANTS = {
    ("A", True),
    ("B", True),
    ("C", True),
    ("C", False),
}


class FormalRQ3Error(RuntimeError):
    pass


def load_inputs(
    bank_path: str | Path,
    plan_path: str | Path,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    bank = json.loads(Path(bank_path).read_text(encoding="utf-8"))
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))

    queries = {
        row["query_id"]: {
            "query_id": row["query_id"],
            "question": row["question"],
            "current_route": row.get("current_route"),
        }
        for row in bank["queries"]
    }
    entries = list(plan["entries"])

    if len(queries) != EXPECTED_QUERIES:
        raise FormalRQ3Error(f"Expected {EXPECTED_QUERIES} queries")
    if len(entries) != EXPECTED_RUNS:
        raise FormalRQ3Error(f"Expected {EXPECTED_RUNS} plan entries")

    by_query: dict[str, set[tuple[str, bool]]] = defaultdict(set)
    seen: set[tuple[str, str, bool]] = set()

    for entry in entries:
        key = (
            entry["query_id"],
            entry["condition"],
            bool(entry["graph_enabled"]),
        )
        if key in seen:
            raise FormalRQ3Error("Duplicate query/condition/graph entry")
        seen.add(key)
        by_query[entry["query_id"]].add(
            (entry["condition"], bool(entry["graph_enabled"]))
        )

    if set(by_query) != set(queries):
        raise FormalRQ3Error("Plan/query-bank query IDs differ")
    if any(variants != EXPECTED_VARIANTS for variants in by_query.values()):
        raise FormalRQ3Error("Plan does not contain the frozen 4-variant matrix")

    return queries, entries


def _require_formal_target() -> None:
    if os.getenv("ERP_ASSISTANT_FORMAL_RUN") != "1":
        raise FormalRQ3Error("Set ERP_ASSISTANT_FORMAL_RUN=1 for formal execution")

    database_url = DatabaseSettings().require_url()
    chroma_path = str(ChromaSettings().path)

    if "formal003" not in database_url.casefold():
        raise FormalRQ3Error("ERP_ASSISTANT_DATABASE_URL does not identify FORMAL003")
    if "formal003" not in chroma_path.casefold():
        raise FormalRQ3Error("ERP_ASSISTANT_CHROMA_PATH does not identify FORMAL003")


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser()
    command.add_argument(
        "--bank",
        default="experiments/query_bank/formal/rq3/rq3_query_bank_v2.json",
    )
    command.add_argument(
        "--plan",
        default="experiments/query_bank/formal/rq3/rq3_execution_plan_v2.json",
    )
    command.add_argument("--output", required=True)
    command.add_argument("--validate-only", action="store_true")
    return command


async def execute(args) -> int:
    queries, entries = load_inputs(args.bank, args.plan)

    if args.validate_only:
        print(
            json.dumps(
                {"queries": len(queries), "runs": len(entries)},
                sort_keys=True,
            )
        )
        return 0

    _require_formal_target()

    app = create_app(
        settings=ApiSettings(semantic_review_api_enabled=False),
        conversation_state_store=ConversationStateStore(
            max_entries=512,
            ttl_seconds=3600,
        ),
    )
    transport = httpx.ASGITransport(app=app)

    output = Path(args.output)
    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "execution": "FORMAL003_RQ3",
        "created_at": utc_now_iso(),
        "automatic_retry": False,
        "records": [],
        "status": "running",
    }
    write_json_atomic(output, report)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://formal003.local",
        timeout=420.0,
    ) as client:
        for entry in entries:
            query = queries[entry["query_id"]]
            payload: dict[str, Any] = {
                "question": query["question"],
                "experimentCondition": entry["condition"],
                "graphEnabled": bool(entry["graph_enabled"]),
            }
            if query["current_route"] is not None:
                payload["context"] = {
                    "currentRoute": query["current_route"],
                }

            started = time.perf_counter()
            try:
                response = await client.post("/api/chat", json=payload)
                latency_ms = round(
                    (time.perf_counter() - started) * 1000,
                    2,
                )

                if response.status_code == 200:
                    body = response.json()
                    answer = str(body.get("answer") or "")
                    status = str(body.get("status") or "unknown")
                    sources = body.get("sources") or []
                    writer_invoked = body.get("answer_mode") == "ollama_grounded"
                else:
                    body = response.json()
                    answer = ""
                    status = f"http_{response.status_code}"
                    sources = []
                    writer_invoked = False
            except Exception as exc:
                latency_ms = round(
                    (time.perf_counter() - started) * 1000,
                    2,
                )
                body = {"error_class": type(exc).__name__}
                answer = ""
                status = "request_error"
                sources = []
                writer_invoked = False

            capture = CaptureRecord(
                run_id=entry["run_id"],
                query_id=entry["query_id"],
                condition=entry["condition"],
                graph_enabled=bool(entry["graph_enabled"]),
                question=query["question"],
                current_route=query["current_route"],
                answer=answer,
                status=status,
                sources=sources,
                end_to_end_latency_ms=latency_ms,
                writer_invoked=writer_invoked,
                raw_response=body,
            )
            report["records"].append(capture.model_dump(mode="json"))
            write_json_atomic(output, report)

    report["status"] = "complete"
    report["summary"] = {
        "runs": len(report["records"]),
        "request_errors": sum(
            row["status"] == "request_error"
            for row in report["records"]
        ),
        "http_errors": sum(
            str(row["status"]).startswith("http_")
            for row in report["records"]
        ),
    }
    write_json_atomic(output, report)

    print(json.dumps(report["summary"], sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(execute(parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
