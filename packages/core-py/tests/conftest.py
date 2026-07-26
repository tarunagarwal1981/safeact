import os

import pytest

from safeact.store import _STORES


@pytest.fixture
def tmp_db(tmp_path):
    """A fresh SQLite file per test, with the process-wide Store cache cleared so
    tests don't leak Store instances into each other."""
    _STORES.clear()
    path = os.path.join(str(tmp_path), "agent.db")
    yield path
    _STORES.clear()
