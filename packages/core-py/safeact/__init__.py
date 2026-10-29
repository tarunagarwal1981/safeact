"""
safeact — at-most-once side effects for AI agents that crash and retry.

Public API (the whole thing):

    from safeact import safe, Saga, InDoubtError, SafeError

    safe          — decorator: make one tool idempotent + durable
    Saga          — context manager: sequence of steps with reverse-order rollback
    InDoubtError  — the honest "we don't know" for the execute->record gap
    SafeError     — base class for every safeact error

Also exported for completeness: FailedCallError, SagaFailed, and Store (for reading
the ledger / resolving in-doubt keys).
"""

from .decorator import safe
from .errors import FailedCallError, HeldForReview, InDoubtError, SafeError, SagaFailed
from .guard import Tainted, allow_all, hold_if, hold_if_tainted, taint
from .saga import Saga
from .store import Store, get_store

__all__ = [
    "safe",
    "Saga",
    "InDoubtError",
    "SafeError",
    "FailedCallError",
    "HeldForReview",
    "SagaFailed",
    "Store",
    "get_store",
    # guard — provenance gate
    "taint",
    "Tainted",
    "hold_if_tainted",
    "hold_if",
    "allow_all",
]

__version__ = "0.1.0"
