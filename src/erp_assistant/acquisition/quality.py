from __future__ import annotations

import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

CRAWL_EXECUTION_QUALITY_CONTRACT_VERSION = 1

BLOCKING_UNCERTAINTY_REASONS = {
    "dynamic_state_restore_failed": "dynamic_state_restore_failures",
    "dynamic_state_exploration_error": "dynamic_state_exploration_errors",
    "navigation_error": "navigation_errors",
    "crawl_fixed_point_stalled": "fixed_point_stalls",
}

_CRAWL_SCOPES = {"full", "module", "screen"}


class CrawlExecutionQualityError(ValueError):
    """The crawl execution-quality contract is missing, malformed, or not certified."""


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrawlExecutionQualityError(f"No se pudo leer {label}.") from exc
    if not isinstance(payload, dict):
        raise CrawlExecutionQualityError(f"{label} debe ser un objeto JSON.")
    return payload


def _nonnegative_int(payload: dict[str, Any], key: str, *, label: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CrawlExecutionQualityError(
            f"{label} no conserva {key} como entero no negativo."
        )
    return value


def _validated_source_identity(
    payload: dict[str, Any], *, label: str
) -> tuple[str, str, str | None]:
    run_key = "run_id" if "run_id" in payload else "source_run_id"
    if run_key not in payload:
        raise CrawlExecutionQualityError(f"{label} no conserva un run_id válido.")
    raw_run_id = str(payload.get(run_key) or "").strip()
    try:
        run_id = str(uuid.UUID(raw_run_id))
    except (TypeError, ValueError) as exc:
        raise CrawlExecutionQualityError(
            f"{label} no conserva un run_id válido."
        ) from exc

    scope_key = "scope" if "scope" in payload else "source_scope"
    if scope_key not in payload:
        raise CrawlExecutionQualityError(
            f"{label} no conserva un scope de crawl válido."
        )
    scope = str(payload.get(scope_key) or "").strip()
    if scope not in _CRAWL_SCOPES:
        raise CrawlExecutionQualityError(
            f"{label} no conserva un scope de crawl válido."
        )

    target_key = "target" if "target" in payload else "source_target"
    if target_key not in payload:
        raise CrawlExecutionQualityError(
            f"{label} no conserva el target del crawl fuente."
        )
    raw_target = payload.get(target_key)
    if raw_target is None:
        target = None
    elif isinstance(raw_target, str) and raw_target.strip():
        target = raw_target.strip()
    else:
        raise CrawlExecutionQualityError(
            f"{label} no conserva un target de crawl válido."
        )

    if scope == "full" and target is not None:
        raise CrawlExecutionQualityError(
            f"{label} conserva target para un crawl FULL."
        )
    if scope in {"module", "screen"} and target is None:
        raise CrawlExecutionQualityError(
            f"{label} no conserva target para un crawl {scope.upper()}."
        )
    return run_id, scope, target


def crawl_result_quality_pins(result: dict[str, Any]) -> dict[str, Any]:
    """Return the persisted crawl-result fields required by quality contract v1."""

    if not isinstance(result, dict):
        raise CrawlExecutionQualityError("El result_payload del crawl fuente es inválido.")
    required = ("run_id", "scope", "target", "pending_routes", "states_pending")
    missing = [name for name in required if name not in result]
    if missing:
        raise CrawlExecutionQualityError(
            "El crawl fuente no conserva el resumen requerido para certificar cobertura: "
            + ", ".join(missing)
        )
    run_id, scope, target = _validated_source_identity(
        result,
        label="El result_payload del crawl fuente",
    )
    pending_routes = _nonnegative_int(
        result,
        "pending_routes",
        label="El result_payload del crawl fuente",
    )
    states_pending = _nonnegative_int(
        result,
        "states_pending",
        label="El result_payload del crawl fuente",
    )
    return {
        "run_id": run_id,
        "scope": scope,
        "target": target,
        "pending_routes": pending_routes,
        "states_pending": states_pending,
    }


def build_crawl_execution_quality(
    *,
    review_dir: Path,
    structural_dir: Path,
    source_crawl_result: dict[str, Any],
    expected_run_id: str,
    expected_scope: str,
    expected_target: str | None,
) -> dict[str, Any]:
    """Build the versioned fail-closed execution-quality contract for one crawl."""

    pins = crawl_result_quality_pins(source_crawl_result)
    if pins["run_id"] != expected_run_id:
        raise CrawlExecutionQualityError(
            "El resumen del crawl fuente no corresponde al run fijado."
        )
    if pins["scope"] != expected_scope:
        raise CrawlExecutionQualityError(
            "El resumen del crawl fuente no corresponde al scope fijado."
        )
    if pins["target"] != expected_target:
        raise CrawlExecutionQualityError(
            "El resumen del crawl fuente no corresponde al target fijado."
        )

    route_frontier_pending = pins["pending_routes"]
    state_frontier_pending = pins["states_pending"]

    state_summary_path = structural_dir / "state_exploration_summary.json"
    if not state_summary_path.is_file():
        raise CrawlExecutionQualityError(
            "El crawl fuente no conserva state_exploration_summary.json final."
        )
    state_summary = _load_json_object(
        state_summary_path,
        label="state_exploration_summary.json",
    )
    artifact_state_pending = _nonnegative_int(
        state_summary,
        "frontier_pending_count",
        label="state_exploration_summary.json",
    )
    if artifact_state_pending != state_frontier_pending:
        raise CrawlExecutionQualityError(
            "El resumen del crawl y state_exploration_summary.json discrepan en "
            "states_pending."
        )

    uncertainty_files = sorted(review_dir.glob("*_uncertainty.json"))
    ui_event_files = [path for path in uncertainty_files if "_ui_events_" in path.name]
    ui_event_names = {path.name for path in ui_event_files}

    events_evaluated = 0
    ui_event_state_restore_failures = 0
    other_error_events = 0
    reasons: Counter[str] = Counter()

    for path in uncertainty_files:
        payload = _load_json_object(path, label=f"evidencia de crawl {path.name}")
        reason = payload.get("reason")
        if reason is not None:
            if not isinstance(reason, str) or not reason.strip():
                raise CrawlExecutionQualityError(
                    f"La evidencia {path.name} conserva reason inválido."
                )
            reasons[reason.strip()] += 1

        if path.name not in ui_event_names:
            continue
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise CrawlExecutionQualityError(
                f"La evidencia de ejecución UI {path.name} es inválida."
            )
        for result in results:
            if not isinstance(result, dict):
                raise CrawlExecutionQualityError(
                    f"La evidencia de ejecución UI {path.name} es inválida."
                )
            events_evaluated += 1
            error = result.get("error")
            if error == "state_restore_failed":
                ui_event_state_restore_failures += 1
            elif error:
                other_error_events += 1

    blocker_counts = {
        field: reasons[reason]
        for reason, field in BLOCKING_UNCERTAINTY_REASONS.items()
    }
    dynamic_state_restore_failures = blocker_counts["dynamic_state_restore_failures"]
    state_restore_failures = (
        ui_event_state_restore_failures + dynamic_state_restore_failures
    )
    blocking_failures = (
        state_restore_failures
        + blocker_counts["dynamic_state_exploration_errors"]
        + blocker_counts["navigation_errors"]
        + blocker_counts["fixed_point_stalls"]
        + route_frontier_pending
        + state_frontier_pending
    )

    return {
        "quality_contract_version": CRAWL_EXECUTION_QUALITY_CONTRACT_VERSION,
        "source_run_id": pins["run_id"],
        "source_scope": pins["scope"],
        "source_target": pins["target"],
        "execution_evidence_present": bool(ui_event_files),
        "ui_event_result_files": len(ui_event_files),
        "events_evaluated": events_evaluated,
        "ui_event_state_restore_failures": ui_event_state_restore_failures,
        "dynamic_state_restore_failures": dynamic_state_restore_failures,
        "state_restore_failures": state_restore_failures,
        "dynamic_state_exploration_errors": blocker_counts[
            "dynamic_state_exploration_errors"
        ],
        "navigation_errors": blocker_counts["navigation_errors"],
        "fixed_point_stalls": blocker_counts["fixed_point_stalls"],
        "route_frontier_pending": route_frontier_pending,
        "state_frontier_pending": state_frontier_pending,
        "other_error_events": other_error_events,
        "blocking_failures": blocking_failures,
        "gate_passed": blocking_failures == 0,
    }


def validate_crawl_execution_quality(
    payload: Any,
    *,
    require_passed: bool = True,
) -> dict[str, Any]:
    """Validate contract v1 and optionally require a certified (gate-passed) crawl."""

    if not isinstance(payload, dict):
        raise CrawlExecutionQualityError(
            "Falta el contrato versionado de calidad de ejecución del crawl."
        )
    if payload.get("quality_contract_version") != CRAWL_EXECUTION_QUALITY_CONTRACT_VERSION:
        raise CrawlExecutionQualityError(
            "La versión del contrato de calidad de ejecución del crawl no es soportada."
        )

    run_id, scope, target = _validated_source_identity(
        payload,
        label="El contrato de calidad del crawl",
    )
    if payload.get("source_run_id") != run_id:
        raise CrawlExecutionQualityError(
            "El contrato de calidad del crawl conserva source_run_id inválido."
        )
    if payload.get("source_scope") != scope:
        raise CrawlExecutionQualityError(
            "El contrato de calidad del crawl conserva source_scope inválido."
        )
    if payload.get("source_target") != target:
        raise CrawlExecutionQualityError(
            "El contrato de calidad del crawl conserva source_target inválido."
        )

    int_fields = (
        "ui_event_result_files",
        "events_evaluated",
        "ui_event_state_restore_failures",
        "dynamic_state_restore_failures",
        "state_restore_failures",
        "dynamic_state_exploration_errors",
        "navigation_errors",
        "fixed_point_stalls",
        "route_frontier_pending",
        "state_frontier_pending",
        "other_error_events",
        "blocking_failures",
    )
    normalized = dict(payload)
    for field in int_fields:
        _nonnegative_int(normalized, field, label="El contrato de calidad del crawl")

    if not isinstance(normalized.get("execution_evidence_present"), bool):
        raise CrawlExecutionQualityError(
            "El contrato de calidad del crawl conserva execution_evidence_present inválido."
        )
    if not isinstance(normalized.get("gate_passed"), bool):
        raise CrawlExecutionQualityError(
            "El contrato de calidad del crawl conserva gate_passed inválido."
        )
    if normalized["execution_evidence_present"] != (
        normalized["ui_event_result_files"] > 0
    ):
        raise CrawlExecutionQualityError(
            "El contrato de calidad del crawl es inconsistente con su evidencia UI."
        )

    expected_restore_failures = (
        normalized["ui_event_state_restore_failures"]
        + normalized["dynamic_state_restore_failures"]
    )
    if normalized["state_restore_failures"] != expected_restore_failures:
        raise CrawlExecutionQualityError(
            "El contrato de calidad del crawl conserva state_restore_failures inconsistente."
        )

    expected_blocking = (
        normalized["state_restore_failures"]
        + normalized["dynamic_state_exploration_errors"]
        + normalized["navigation_errors"]
        + normalized["fixed_point_stalls"]
        + normalized["route_frontier_pending"]
        + normalized["state_frontier_pending"]
    )
    if normalized["blocking_failures"] != expected_blocking:
        raise CrawlExecutionQualityError(
            "El contrato de calidad del crawl conserva blocking_failures inconsistente."
        )
    expected_passed = expected_blocking == 0
    if normalized["gate_passed"] is not expected_passed:
        raise CrawlExecutionQualityError(
            "El contrato de calidad del crawl conserva gate_passed inconsistente."
        )
    if require_passed and not expected_passed:
        raise CrawlExecutionQualityError(
            "El crawl fuente no supera el contrato de calidad estructural."
        )
    return normalized


def validate_certified_quality_source(
    payload: Any,
    *,
    source_run_id: uuid.UUID | str,
    source_scope: str | None = None,
    source_target: str | None = None,
    check_target: bool = False,
) -> dict[str, Any]:
    """Require certified quality bound to the expected crawl lineage."""

    quality = validate_crawl_execution_quality(payload, require_passed=True)
    try:
        expected_run_id = str(uuid.UUID(str(source_run_id)))
        actual_run_id = str(uuid.UUID(str(quality["source_run_id"])))
    except (TypeError, ValueError) as exc:
        raise CrawlExecutionQualityError(
            "La provenance no conserva un source_run_id de crawl válido."
        ) from exc
    if actual_run_id != expected_run_id:
        raise CrawlExecutionQualityError(
            "El contrato de calidad no corresponde al source_crawl_job_id fijado."
        )

    if source_scope is not None:
        expected_scope = str(source_scope).strip()
        if expected_scope not in _CRAWL_SCOPES:
            raise CrawlExecutionQualityError(
                "La provenance esperada conserva un scope de crawl inválido."
            )
        if quality["source_scope"] != expected_scope:
            raise CrawlExecutionQualityError(
                "El contrato de calidad no corresponde al scope del crawl fijado."
            )

    if check_target:
        if source_target is None:
            expected_target = None
        elif isinstance(source_target, str) and source_target.strip():
            expected_target = source_target.strip()
        else:
            raise CrawlExecutionQualityError(
                "La provenance esperada conserva un target de crawl inválido."
            )
        if quality["source_target"] != expected_target:
            raise CrawlExecutionQualityError(
                "El contrato de calidad no corresponde al target del crawl fijado."
            )
    return quality


def validate_matching_certified_quality(*payloads: Any) -> dict[str, Any]:
    """Require one certified contract and exact equality across provenance copies."""

    if not payloads:
        raise CrawlExecutionQualityError(
            "No se recibió provenance de calidad de crawl para comparar."
        )
    qualities = [
        validate_crawl_execution_quality(payload, require_passed=True)
        for payload in payloads
    ]
    first = qualities[0]
    if any(value != first for value in qualities[1:]):
        raise CrawlExecutionQualityError(
            "Las copias de provenance de calidad de crawl no coinciden."
        )
    return first
