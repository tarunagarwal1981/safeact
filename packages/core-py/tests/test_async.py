"""Async tools get the same exactly-once + in-doubt guarantees as sync ones.

Uses asyncio.run() per call so no pytest-asyncio dependency is needed.
"""

import asyncio

import pytest

from safeact import InDoubtError, safe
from safeact.store import get_store


def test_async_runs_exactly_once(tmp_db):
    calls = {"n": 0}

    @safe(store=tmp_db)
    async def send_email(key, to):
        calls["n"] += 1
        await asyncio.sleep(0)  # a real await point
        return {"to": to, "execution": calls["n"]}

    async def main():
        r1 = await send_email("welcome-42", "a@b.com")
        r2 = await send_email("welcome-42", "a@b.com")  # retry, same key
        return r1, r2

    r1, r2 = asyncio.run(main())
    assert calls["n"] == 1, "the async side effect must fire exactly once"
    assert r1 == r2 == {"to": "a@b.com", "execution": 1}


def test_async_crash_in_gap_is_in_doubt(tmp_db):
    calls = {"n": 0}

    @safe(store=tmp_db)
    async def send_email(key, to):
        calls["n"] += 1
        return {"to": to}

    store = get_store(tmp_db)

    def die_in_gap(key):
        raise RuntimeError("process died after send, before record")

    store._gap_hook = die_in_gap
    with pytest.raises(RuntimeError):
        asyncio.run(send_email("welcome-42", "a@b.com"))
    assert calls["n"] == 1  # the effect DID happen once

    store._gap_hook = lambda key: None
    with pytest.raises(InDoubtError):
        asyncio.run(send_email("welcome-42", "a@b.com"))  # retry
    assert calls["n"] == 1  # retry did NOT re-run


def test_async_tool_receives_the_key(tmp_db):
    seen = {}

    @safe(store=tmp_db)
    async def charge(key, amount):
        seen["key"] = key  # forwardable to a downstream idempotency header
        return amount

    asyncio.run(charge("pay-1", 100))
    assert seen["key"] == "pay-1"
