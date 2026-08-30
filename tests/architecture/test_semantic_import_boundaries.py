from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_semantic_import_boundaries_are_acyclic_from_fresh_process():
    code = """
import importlib

for name in (
    'src.analysis.evidence',
    'src.database.services.semantic_chroma_sync_service',
    'src.database.services.semantic_retrieval_authorization_service',
    'src.database.services',
):
    importlib.import_module(name)
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        result.stderr or result.stdout
    )
