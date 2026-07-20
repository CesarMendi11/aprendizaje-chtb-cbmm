import os
import subprocess

import pytest


@pytest.mark.postgresql
def test_postgres_migration_cycle():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL no configurada")
    env = {**os.environ, "ERP_ASSISTANT_DATABASE_URL": url}
    for command in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
        subprocess.run(["alembic", *command], check=True, env=env)
