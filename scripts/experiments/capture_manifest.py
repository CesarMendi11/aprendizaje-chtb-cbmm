from __future__ import annotations

import argparse
import importlib.metadata
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from erp_assistant.config.chroma_settings import ChromaSettings
from erp_assistant.config.neo4j_settings import Neo4jSettings
from erp_assistant.config.paths import PROJECT_ROOT
from erp_assistant.config.pipeline_settings import PipelineSettings
from erp_assistant.persistence.postgres.session import session_scope
from erp_assistant.projections.neo4j.repository import Neo4jRepository

from scripts.common.database import database_engine
from scripts.common.neo4j import neo4j_client, safe_neo4j_error
from scripts.experiments.common import (
    project_relative,
    sha256_file,
    utc_now_iso,
    write_json_atomic,
)
from scripts.status.database_status import collect_status


PACKAGE_NAMES = (
    "alembic",
    "chromadb",
    "fastapi",
    "neo4j",
    "playwright",
    "psycopg",
    "pydantic",
    "PyYAML",
    "SQLAlchemy",
    "uvicorn",
)


def _run_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def collect_git_state() -> dict[str, Any]:
    porcelain = _run_git("status", "--porcelain")
    return {
        "commit": _run_git("rev-parse", "HEAD"),
        "branch": _run_git("branch", "--show-current") or None,
        "worktree_clean": not bool(porcelain),
    }


def collect_package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in PACKAGE_NAMES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def collect_database_state() -> dict[str, Any]:
    try:
        with session_scope(database_engine()) as session:
            return collect_status(session)
    except Exception as exc:
        return {"connectivity": "error", "error": str(exc)[:500]}


def collect_neo4j_state() -> dict[str, Any]:
    settings = Neo4jSettings()
    try:
        with neo4j_client(settings) as client:
            verification = client.verify()
            status = Neo4jRepository(client).status()
            return {
                "connectivity": "ok",
                "database": settings.database,
                "server_agent": verification.get("agent"),
                "protocol_version": verification.get("protocol_version"),
                **status,
            }
    except Exception as exc:
        return {
            "connectivity": "error",
            "database": settings.database,
            "error": safe_neo4j_error(exc, settings),
        }


def collect_chroma_state() -> dict[str, Any]:
    path = ChromaSettings().path
    if not path.exists():
        return {
            "path": project_relative(path),
            "exists": False,
            "files": 0,
            "bytes": 0,
        }
    files = [item for item in path.rglob("*") if item.is_file()]
    return {
        "path": project_relative(path),
        "exists": True,
        "files": len(files),
        "bytes": sum(item.stat().st_size for item in files),
    }


def collect_file_hashes() -> dict[str, str | None]:
    candidates = (
        PROJECT_ROOT / "configs" / "cbmm.yaml",
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "requirements.txt",
        PROJECT_ROOT / "admin-ui" / "package-lock.json",
    )
    return {
        project_relative(path): sha256_file(path) if path.is_file() else None
        for path in candidates
    }


def build_manifest() -> dict[str, Any]:
    pipeline = PipelineSettings()
    return {
        "schema_version": "1.0.0",
        "manifest_type": "experiment_environment",
        "captured_at": utc_now_iso(),
        "git": collect_git_state(),
        "runtime": {
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "packages": collect_package_versions(),
        },
        "configuration": {
            "file_sha256": collect_file_hashes(),
            "crawl_profile": project_relative(pipeline.crawl_profile_path),
            "pipeline_runs_root": project_relative(pipeline.runs_root),
            "embedding_model": os.getenv(
                "ERP_ASSISTANT_EMBEDDING_MODEL", "qwen3-embedding:0.6b"
            ),
            "generation_model": os.getenv(
                "ERP_ASSISTANT_GENERATION_MODEL", "llama3.2:3b"
            ),
            "ollama_endpoint_configured": bool(os.getenv("ERP_ASSISTANT_OLLAMA_URL")),
        },
        "postgresql": collect_database_state(),
        "neo4j": collect_neo4j_state(),
        "chroma": collect_chroma_state(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Captura un manifiesto reproducible del entorno experimental sin secretos."
    )
    parser.add_argument(
        "--output",
        default="experiments/results/t0_manifest.json",
        help="Ruta JSON de salida relativa al proyecto o absoluta.",
    )
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Retorna código 2 si PostgreSQL o Neo4j no están disponibles.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_manifest()
    output = write_json_atomic(args.output, manifest)
    print(output)
    if args.require_ready and (
        manifest["postgresql"].get("connectivity") != "ok"
        or manifest["neo4j"].get("connectivity") != "ok"
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
