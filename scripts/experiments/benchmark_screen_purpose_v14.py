from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import subprocess
from pathlib import Path
from typing import Any

from erp_assistant.config.paths import PROJECT_ROOT
from scripts.experiments.common import sha256_file, utc_now_iso, write_json_atomic
from scripts.experiments.pilot_screen_purpose_v14 import run as run_pilot

DEFAULT_MODELS = (
    "phi4-mini:3.8b",
    "qwen2.5:14b",
    "qwen3.5:9b",
)
DEFAULT_SCREEN_SET = PROJECT_ROOT / "experiments/semantic/development_screens_v1.json"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark read-only de screen-purpose-v14 sobre un conjunto de desarrollo. "
            "No sustituye la revisión humana ni modifica SemanticProposal."
        )
    )
    parser.add_argument(
        "--screen-set",
        default=str(DEFAULT_SCREEN_SET),
        help="JSON de pantallas development",
    )
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help="modelo Ollama; repetir para comparar varios",
    )
    parser.add_argument("--output", required=True, help="JSON de resultados")
    parser.add_argument(
        "--review-csv",
        help="CSV opcional para revisión humana ciega por alias de modelo",
    )
    return parser.parse_args(argv)


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def load_development_set(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with source.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("purpose") != "development_only":
        raise ValueError("El screen-set debe declarar purpose=development_only")
    if payload.get("exclude_from_final_m2_quality_evaluation") is not True:
        raise ValueError("El screen-set debe excluirse explícitamente de evaluación M2 final")
    screens = payload.get("screens")
    if not isinstance(screens, list) or len(screens) < 3:
        raise ValueError("El screen-set requiere al menos tres pantallas")

    seen: set[str] = set()
    for index, row in enumerate(screens):
        if not isinstance(row, dict):
            raise ValueError(f"Pantalla development inválida en posición {index}")
        required = ("screen_id", "title", "route", "profile")
        if any(not str(row.get(key, "")).strip() for key in required):
            raise ValueError(f"Pantalla development incompleta en posición {index}")
        screen_id = str(row["screen_id"]).strip()
        if screen_id in seen:
            raise ValueError(f"Screen duplicada en development: {screen_id}")
        seen.add(screen_id)
    return payload


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return round(float(ordered[rank]), 3)


def summarize_model_result(result: dict[str, Any]) -> dict[str, Any]:
    rows = result["results"]
    generated = [row for row in rows if row.get("status") == "generated"]
    ineligible = [
        row
        for row in rows
        if row.get("status") != "generated" and row.get("error_type") == "InferenceGroundingError"
    ]
    generation_failures = [
        row
        for row in rows
        if row.get("status") != "generated" and row.get("error_type") != "InferenceGroundingError"
    ]
    latencies = [
        float(row["generation_elapsed_ms"])
        for row in rows
        if isinstance(row.get("generation_elapsed_ms"), (int, float))
        and row.get("error_type") != "InferenceGroundingError"
    ]
    claims = sum(len(row.get("functional_claims", [])) for row in generated)
    warnings = sum(len(row.get("warnings", [])) for row in generated)
    return {
        "screens": len(rows),
        "generated": len(generated),
        "ineligible": len(ineligible),
        "generation_failures": len(generation_failures),
        "claims": claims,
        "warnings": warnings,
        "latency_ms": {
            "count": len(latencies),
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
            "median": round(statistics.median(latencies), 3) if latencies else None,
            "p95": _percentile(latencies, 0.95),
            "max": round(max(latencies), 3) if latencies else None,
        },
    }


def _model_aliases(models: list[str], screen_set_sha256: str) -> dict[str, str]:
    ranked = sorted(
        models,
        key=lambda model: hashlib.sha256(
            f"{screen_set_sha256}:{model}".encode("utf-8")
        ).hexdigest(),
    )
    return {model: f"M{index:02d}" for index, model in enumerate(ranked, start=1)}


def _validate_result_matches_set(
    result: dict[str, Any],
    screen_set: dict[str, Any],
) -> None:
    expected = {row["screen_id"]: row for row in screen_set["screens"]}
    actual = {row["screen_id"]: row for row in result["results"]}
    if set(actual) != set(expected):
        raise RuntimeError("El benchmark no devolvió exactamente las pantallas development")
    for screen_id, configured in expected.items():
        observed = actual[screen_id]
        if observed["screen_title"] != configured["title"]:
            raise RuntimeError(f"Title drift para {screen_id}")
        if observed["screen_route"] != configured["route"]:
            raise RuntimeError(f"Route drift para {screen_id}")
    if result.get("semantic_persistence_unchanged") is not True:
        raise RuntimeError("El benchmark detectó mutación de persistencia semántica")


def build_benchmark(
    screen_set_path: str | Path,
    *,
    models: list[str],
) -> dict[str, Any]:
    unique_models = [model.strip() for model in models if str(model).strip()]
    if not unique_models or len(set(unique_models)) != len(unique_models):
        raise ValueError("Los modelos deben ser no vacíos y sin duplicados")

    screen_set = load_development_set(screen_set_path)
    screen_ids = [row["screen_id"] for row in screen_set["screens"]]
    screen_set_hash = sha256_file(screen_set_path)
    results: dict[str, Any] = {}

    active_version: str | None = None
    active_version_id: str | None = None
    for model in unique_models:
        result = run_pilot(screen_ids, model=model)
        _validate_result_matches_set(result, screen_set)
        if result["knowledge_version"] != screen_set.get("knowledge_version"):
            raise RuntimeError(
                "La ACTIVE no coincide con la KnowledgeVersion fijada en development"
            )
        if active_version is None:
            active_version = result["knowledge_version"]
            active_version_id = result["knowledge_version_id"]
        elif (
            result["knowledge_version"] != active_version
            or result["knowledge_version_id"] != active_version_id
        ):
            raise RuntimeError("La ACTIVE cambió durante el benchmark")
        results[model] = {
            "summary": summarize_model_result(result),
            "run": result,
        }

    return {
        "benchmark_id": "m2-v14-development-model-benchmark-v1",
        "purpose": "development_only_model_selection",
        "generated_at": utc_now_iso(),
        "git_head": _git_head(),
        "screen_set": {
            "set_id": screen_set["set_id"],
            "sha256": screen_set_hash,
            "count": len(screen_set["screens"]),
            "exclude_from_final_m2_quality_evaluation": True,
            "screens": screen_set["screens"],
        },
        "models": unique_models,
        "model_aliases": _model_aliases(unique_models, screen_set_hash),
        "knowledge_version": active_version,
        "knowledge_version_id": active_version_id,
        "results": results,
        "selection_status": "pending_human_review",
        "selection_rule": (
            "No seleccionar por estilo. Revisar corrección semántica, soporte de evidencia, "
            "utilidad, omisiones, sobreinferencias y latencia sobre development."
        ),
    }


def write_review_csv(path: str | Path, benchmark: dict[str, Any]) -> Path:
    destination = Path(path)
    if not destination.is_absolute():
        destination = PROJECT_ROOT / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    profiles = {row["screen_id"]: row["profile"] for row in benchmark["screen_set"]["screens"]}

    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "model_alias",
                "screen_id",
                "screen_title",
                "profile",
                "status",
                "item_type",
                "item_index",
                "text",
                "evidence_refs",
                "correctness",
                "evidence_support",
                "usefulness",
                "overinference",
                "review_decision",
                "review_notes",
            ],
        )
        writer.writeheader()
        aliases = benchmark["model_aliases"]
        for model in benchmark["models"]:
            alias = aliases[model]
            for row in benchmark["results"][model]["run"]["results"]:
                common = {
                    "model_alias": alias,
                    "screen_id": row["screen_id"],
                    "screen_title": row["screen_title"],
                    "profile": profiles[row["screen_id"]],
                    "status": row["status"],
                }
                if row["status"] != "generated":
                    writer.writerow(
                        {
                            **common,
                            "item_type": "generation_status",
                            "text": row.get("error", ""),
                        }
                    )
                    continue
                writer.writerow(
                    {
                        **common,
                        "item_type": "purpose_summary",
                        "item_index": 0,
                        "text": row["purpose_summary"],
                    }
                )
                for index, claim in enumerate(row["functional_claims"], start=1):
                    writer.writerow(
                        {
                            **common,
                            "item_type": "functional_claim",
                            "item_index": index,
                            "text": claim["statement"],
                            "evidence_refs": " | ".join(claim["evidence_refs"]),
                        }
                    )
                for item_type, values in (
                    ("limitation", row.get("limitations", [])),
                    ("uncertainty", row.get("uncertainties", [])),
                ):
                    for index, text in enumerate(values, start=1):
                        writer.writerow(
                            {
                                **common,
                                "item_type": item_type,
                                "item_index": index,
                                "text": text,
                            }
                        )
    return destination


def main(argv=None):
    args = parse_args(argv)
    models = args.models or list(DEFAULT_MODELS)
    benchmark = build_benchmark(args.screen_set, models=models)
    output = write_json_atomic(args.output, benchmark)
    review = write_review_csv(args.review_csv, benchmark) if args.review_csv else None

    print(f"benchmark: {output}")
    print(f"git_head: {benchmark['git_head']}")
    print(f"knowledge_version: {benchmark['knowledge_version']}")
    print(f"screen_set: {benchmark['screen_set']['set_id']} ({benchmark['screen_set']['count']})")
    for model in benchmark["models"]:
        summary = benchmark["results"][model]["summary"]
        latency = summary["latency_ms"]
        print(
            f"{model}: generated={summary['generated']}/{summary['screens']} "
            f"ineligible={summary['ineligible']} failures={summary['generation_failures']} "
            f"claims={summary['claims']} median_ms={latency['median']} p95_ms={latency['p95']}"
        )
    if review is not None:
        print(f"review_csv: {review}")
    print("selection_status: pending_human_review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
