"""The complete error surface: four names, no more."""

from __future__ import annotations


class SafeError(Exception):
    """Base class for every error safeact raises. Catch this to catch all."""


class InDoubtError(SafeError):
    """
    Raised when a key is stuck in the unrecoverable gap: the side effect was
    executing when the process died, and we cannot know whether it completed.

    This is NOT a bug and NOT a dead end. It is an honest "we don't know" that
    carries a receipt so you (or a reconciliation pass) can go ask the downstream
    system what actually happened, then close it out with ``store.resolve(key, ...)``.
    """

    def __init__(self, record):
        # `record` is a store.Record — the durable receipt.
        self.key = record.key
        self.tool = record.fn_name
        self.args_hash = record.args_hash
        self.attempted_at = record.created_at
        super().__init__(
            f"key {record.key!r} is IN_DOUBT: tool {record.fn_name!r} was mid-execution "
            f"when the process died (first attempt {record.created_at}, args_hash "
            f"{record.args_hash}). The side effect MAY have happened. Reconcile with the "
            f"downstream system, then call store.resolve({record.key!r}, happened=...). "
            f"Refusing to blindly re-run."
        )


class FailedCallError(SafeError):
    """
    Raised on a same-key call after that key already FAILED. safeact refuses to
    re-run a failed key by default: it cannot know *why* it failed, and re-running
    either hammers a doomed action or risks duplicating a partial success. Use a
    NEW key to genuinely retry.
    """

    def __init__(self, record):
        self.key = record.key
        self.original_error = record.error
        super().__init__(
            f"key {record.key!r} previously FAILED ({record.error}). Refusing to re-run; "
            f"use a new idempotency key to retry."
        )


class SagaFailed(SafeError):
    """
    Raised when a Saga step fails. By the time this propagates, the completed steps
    have been compensated in reverse order. If a compensator was itself in-doubt,
    rollback halted and the offending keys are listed in ``.in_doubt``.
    """

    def __init__(self, saga_id, original, compensated, in_doubt=None):
        self.saga_id = saga_id
        self.original = original
        self.compensated = list(compensated)
        self.in_doubt = list(in_doubt or [])
        msg = (
            f"saga {saga_id!r} failed: {original!r}. "
            f"Compensated (reverse order): {self.compensated or 'none'}."
        )
        if self.in_doubt:
            msg += f" ROLLBACK INCOMPLETE — in-doubt compensators: {self.in_doubt}."
        super().__init__(msg)
