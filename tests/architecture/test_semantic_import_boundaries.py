from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_semantic_import_boundaries_are_acyclic_from_fresh_process():
    code = """
import importlib

for name in (
    'erp_assistant.semantic.evidence',
    'erp_assistant.projections.chroma.semantic_sync_service',
    'erp_assistant.semantic.services.semantic_retrieval_authorization_service',
    'erp_assistant.semantic.services.semantic_proposal_service',
):
    importlib.import_module(name)
"""

    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
