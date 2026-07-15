import json

from scripts.inspect_ui_event_results import build_audit, latest_result_files


def write_result(path, route, result):
    path.write_text(
        json.dumps(
            {
                "route": route,
                "source_state_id": "ui_state:source",
                "results": [result],
            }
        ),
        encoding="utf-8",
    )


def candidate(category="open_dropdown", label="Seleccione"):
    return {"event_category": category, "label": label}


def test_latest_result_files_keeps_only_newest_file_per_route(tmp_path):
    older = tmp_path / "admin_test_ui_events_a_20260715_010000_uncertainty.json"
    newer = tmp_path / "admin_test_ui_events_b_20260715_020000_uncertainty.json"
    other = tmp_path / "admin_other_ui_events_c_20260715_015000_uncertainty.json"

    write_result(
        older,
        "/admin/test",
        {"candidate": candidate(), "changed": False, "error": "old"},
    )
    write_result(
        newer,
        "/admin/test",
        {
            "candidate": candidate(),
            "changed": True,
            "interaction_succeeded": True,
            "outcome": "changed",
        },
    )
    write_result(
        other,
        "/admin/other",
        {
            "candidate": candidate("change_pagination", "Siguiente"),
            "changed": False,
            "error": "state_restore_failed",
        },
    )

    files = latest_result_files(tmp_path)

    assert set(files) == {newer, other}


def test_build_audit_summarizes_execution_outcomes(tmp_path):
    changed = tmp_path / "admin_a_ui_events_a_20260715_020000_uncertainty.json"
    failed = tmp_path / "admin_b_ui_events_b_20260715_020001_uncertainty.json"
    write_result(
        changed,
        "/admin/a",
        {
            "candidate": candidate(),
            "changed": True,
            "interaction_succeeded": True,
            "restore_strategy": "direct_route",
            "outcome": "changed",
        },
    )
    write_result(
        failed,
        "/admin/b",
        {
            "candidate": candidate("open_date_picker", "Calendario"),
            "changed": False,
            "error": "state_restore_failed",
            "restore_strategy": "failed",
        },
    )

    audit = build_audit([changed, failed])

    assert audit["events_count"] == 2
    assert audit["outcomes"] == {"changed": 1, "restore_failed": 1}
    assert audit["changed_categories"] == {"open_dropdown": 1}
    assert audit["errors"] == {"state_restore_failed": 1}
