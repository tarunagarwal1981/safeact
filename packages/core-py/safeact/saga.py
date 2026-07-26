"""
Saga — run a sequence of durable steps with automatic reverse-order compensation.

Steps are plain callables. The Saga owns idempotency-key generation, so there is no
key to forget and replay is deterministic:

    step key         =  "{saga_id}:{index}:{fn_name}"
    compensation key  =  "comp:{step_key}"

Because those keys are deterministic, re-running the same `with Saga(saga_id): ...`
block after a crash re-attaches to prior progress instead of redoing it, and running
rollback twice is a no-op (no double-undo).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .errors import InDoubtError, SagaFailed
from .store import get_store


class _Completed:
    __slots__ = ("key", "name", "result", "compensate")

    def __init__(self, key, name, result, compensate):
        self.key = key
        self.name = name
        self.result = result
        self.compensate = compensate


class Saga:
    def __init__(self, saga_id: str, store):
        if not isinstance(saga_id, str) or not saga_id:
            raise TypeError("saga_id must be a non-empty string")
        self.saga_id = saga_id
        self.store = get_store(store)
        self._index = 0
        self._completed: list[_Completed] = []
        self._compensated: list[str] = []  # readable after a crash mid-rollback

    def __enter__(self) -> "Saga":
        return self

    def step(self, fn: Callable, *args, compensate: Optional[Callable] = None, **kwargs) -> Any:
        """
        Run one durable step now. On a fresh run it executes `fn`; on replay it
        returns the stored result without re-executing. If `fn` raises, the store
        records FAILED and the exception propagates — which triggers rollback via
        __exit__.
        """
        name = getattr(fn, "__name__", "step")
        key = f"{self.saga_id}:{self._index}:{name}"
        self._index += 1

        # If `fn` raises here, we do NOT append to _completed — an incomplete step is
        # not compensated (only completed steps are). The exception flows to __exit__.
        result = self.store.run(key, fn, args, kwargs, fn_name=name)
        self._completed.append(_Completed(key, name, result, compensate))
        return result

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            return False  # success: nothing to compensate

        # A step failed. Compensate completed steps in reverse order.
        self._compensated = []
        try:
            self._rollback()
        except InDoubtError as ide:
            # A compensator itself died in its own gap on a prior run. Halt rather
            # than risk a double-undo, and surface exactly which key is in doubt.
            raise SagaFailed(
                self.saga_id, exc, self._compensated, in_doubt=[ide.key]
            ) from ide

        raise SagaFailed(self.saga_id, exc, self._compensated) from exc

    def _rollback(self) -> None:
        for st in reversed(self._completed):
            if st.compensate is None:
                continue
            comp_key = f"comp:{st.key}"
            # Each compensator runs through the SAME store machinery, keyed
            # deterministically -> running rollback twice is a no-op (idempotent undo).
            # The compensator receives the step's result so it knows what to undo.
            self.store.run(comp_key, st.compensate, (st.result,), {}, fn_name=f"comp:{st.name}")
            self._compensated.append(st.name)
