"""Drive host-owned database steps separately from blocking I/O.

Generators run under the caller's Store lock. A yielded callable must not access
the Store except through its own explicit, short host transactions.
"""
from __future__ import annotations

from contextlib import nullcontext


def drive(steps, lock=None):
    value, error = None, None
    while True:
        with lock if lock is not None else nullcontext():
            try:
                operation = steps.throw(error) if error is not None else steps.send(value)
            except StopIteration as done:
                return done.value
        try:
            value = operation()
            error = None
        except BaseException as exc:
            value, error = None, exc
