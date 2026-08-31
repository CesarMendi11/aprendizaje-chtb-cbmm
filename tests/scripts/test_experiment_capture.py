from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from scripts.experiments.capture_job import _duration_ms, build_job_snapshot
from scripts.experiments.common import redact_sensitive, sha256_file


def test_redact_sensitive_recurses_without_exposing_secret_values():
    payload = {
        "safe": "value",
        "password": "secret-1",
        "nested": {
            "authorization_header": "Bearer secret-2",
            "items": [{"token": "secret-3"}, {"label": "Año"}],
        },
    }

    assert redact_sensitive(payload) == {
        "safe": "value",
        "password": "[redacted]",
        "nested": {
            "authorization_header": "[redacted]",
            "items": [{"token": "[redacted]"}, {"label": "Año"}],
        },
    }


def test_sha256_file_matches_known_digest(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_bytes(b"chat-cbmm")

    assert sha256_file(path) == hashlib.sha256(b"chat-cbmm").hexdigest()


def test_duration_ms_uses_job_timestamps():
    start = datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=1.234)

    assert _duration_ms(start, end) == 1234
    assert _duration_ms(None, end) is None


def test_build_job_snapshot_preserves_metrics_and_redacts_parameters():
    requested = datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)
    started = requested + timedelta(seconds=2)
    finished = started + timedelta(seconds=5)
    job = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        kind=SimpleNamespace(value="crawl"),
        status=SimpleNamespace(value="succeeded"),
        scope=SimpleNamespace(value="full"),
        target=None,
        profile_name="cbmm",
        erp_id=None,
        knowledge_version_id=None,
        request_source="admin_api",
        stage="completed",
        progress_current=1,
        progress_total=1,
        parameters={"headless": False, "token": "do-not-export"},
        checkpoint={"visited": 52},
        result_payload={"functional_screens": 52},
        error_summary=None,
        requested_at=requested,
        started_at=started,
        finished_at=finished,
        updated_at=finished,
    )

    snapshot = build_job_snapshot(job)

    assert snapshot["job"]["parameters"]["token"] == "[redacted]"
    assert snapshot["job"]["queue_wait_ms"] == 2000
    assert snapshot["job"]["execution_ms"] == 5000
    assert snapshot["job"]["wall_clock_ms"] == 7000
    assert snapshot["job"]["result_payload"]["functional_screens"] == 52
