"""
Guarantee: the idempotency key is mandatory and explicit. Forgetting it fails loudly
— never a silent unguarded run (the worst failure mode). The tool also receives the
key so it can forward it downstream.
"""

import pytest

from safeact import safe


def test_missing_key_raises(tmp_db):
    @safe(store=tmp_db)
    def ping(key):
        return "pong"

    with pytest.raises(TypeError):
        ping()  # no key supplied -> Python arg-binding failure, loud


def test_empty_or_non_string_key_rejected(tmp_db):
    @safe(store=tmp_db)
    def act(key, x):
        return x

    with pytest.raises(TypeError):
        act("", 1)  # empty string is not a usable key
    with pytest.raises(TypeError):
        act(123, 1)  # non-string key


def test_tool_receives_the_key_for_downstream_forwarding(tmp_db):
    seen = {}

    @safe(store=tmp_db)
    def charge(key, amount):
        seen["key"] = key  # e.g. forward as Stripe Idempotency-Key
        return {"amount": amount}

    charge("charge-77", 100)
    assert seen["key"] == "charge-77"
