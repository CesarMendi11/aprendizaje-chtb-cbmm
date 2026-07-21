import os
import subprocess
import sys
from urllib.parse import urlsplit

import pytest


@pytest.mark.postgresql
def test_postgres_migration_cycle():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL no configurada")
    database = urlsplit(url).path.lstrip("/").casefold()
    if "semantic_test" not in database and "test" not in database:
        pytest.fail("TEST_DATABASE_URL no apunta a una base temporal con marcador seguro")
    env = {**os.environ, "ERP_ASSISTANT_DATABASE_URL": url}
    for command in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
        subprocess.run([sys.executable, "-m", "alembic", *command], check=True, env=env)
