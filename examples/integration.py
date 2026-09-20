"""Runnable companion to docs/integration.md - copy any block into your code.

    python examples/integration.py

Requires nothing beyond beutilogs and the standard library.
"""

import logging
import sys

import beutilogs


# --- 1. Route stdlib `logging` errors through the pretty report ------------- #
class BeutilogsHandler(logging.Handler):
    """Attach to any logger so every logged exception gets a full report."""

    def emit(self, record):
        if record.exc_info:
            exc = record.exc_info[1]
            sys.stderr.write(beutilogs.capture(exc, color=False) + "\n")


def demo_logging():
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    log = logging.getLogger("app")
    log.addHandler(BeutilogsHandler())
    try:
        {}["missing"]
    except Exception:
        log.exception("job failed")  # stdlib logging + beutilogs report


# --- 2. One call per error you catch yourself ------------------------------- #
def demo_catch():
    try:
        int("not a number")
    except ValueError as exc:
        record = beutilogs.log(exc, path="errors.jsonl")  # report + JSON line
    return record


# --- 3. Use `capture()` in tests instead of eyeballing a traceback ---------- #
def test_that_the_error_points_at_the_right_line():
    try:
        _divide(1, 0)
    except ZeroDivisionError as exc:
        text = beutilogs.capture(exc, color=False)
    assert "return a / b" in text       # the real source line is present
    assert "bug here" in text


def _divide(a, b):
    return a / b


if __name__ == "__main__":
    demo_logging()
    demo_catch()
    test_that_the_error_points_at_the_right_line()
    print("integration example: ok")
