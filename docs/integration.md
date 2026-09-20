# Integrating beutilogs into your code

A practical guide, from "one line" to "production". Every snippet uses only
`beutilogs` and the standard library. A runnable version of the first three
sections lives in [`examples/integration.py`](../examples/integration.py) —
`python examples/integration.py`.

**Contents**

1. [Install](#1-install)
2. [The one-liner: log an error you caught](#2-the-one-liner-log-an-error-you-caught)
3. [The safety net: never lose an uncaught error](#3-the-safety-net-never-lose-an-uncaught-error)
4. [Feed it from the standard `logging` module](#4-feed-it-from-the-standard-logging-module)
5. [Web frameworks](#5-web-frameworks)
6. [Async, threads, and background jobs](#6-async-threads-and-background-jobs)
7. [Keep errors as data (JSON lines)](#7-keep-errors-as-data-json-lines)
8. [Test that your errors point at the right line](#8-test-that-your-errors-point-at-the-right-line)
9. [Production checklist](#9-production-checklist)

---

## 1. Install

```console
$ pip install beutilogs
```

No dependencies, no configuration files, nothing to initialise.

---

## 2. The one-liner: log an error you caught

You already have `try/except` blocks. Change where the report goes:

```python
import beutilogs

try:
    result = compute(order)
except Exception as exc:
    beutilogs.log(exc, path="errors.jsonl")   # pretty report + one JSON line
    raise                                      # or handle it
```

Prefer no file? `beutilogs.report(exc)` prints and returns nothing useful;
`beutilogs.capture(exc)` returns the report as a string (great for a web
response or a test assertion).

```python
text = beutilogs.capture(exc, color=False)     # no ANSI, safe for files
```

**Choose the right call**

| You want | Use |
| --- | --- |
| A human-readable report on stderr | `beutilogs.report(exc)` |
| Structured data on disk, plus the report | `beutilogs.log(exc, path=...)` |
| A string to embed elsewhere | `beutilogs.capture(exc, color=False)` |

---

## 3. The safety net: never lose an uncaught error

Call `install()` **once**, at the top of your entry point. It replaces
`sys.excepthook` and `threading.excepthook`, so an uncaught error anywhere in
your program produces a full beutilogs report instead of a bare traceback.

```python
# main.py
import beutilogs

beutilogs.install(path="errors.jsonl")   # drop `path=` to only print

from app import run

run()
```

- `KeyboardInterrupt` (Ctrl-C) and `SystemExit` keep their normal behaviour.
- Works for threads started with `threading.Thread` too.
- Returns an `uninstall()` callable if you need to restore the old hooks
  (useful in tests, or in a library that should not hijack the host app):

```python
uninstall = beutilogs.install()
try:
    run()
finally:
    uninstall()
```

> Only install this in the **application**, never in a library. Libraries
> should call `report()` and re-raise, not own `sys.excepthook`.

---

## 4. Feed it from the standard `logging` module

If your code already logs with `logging`, add this handler once and every
`logger.exception(...)` / `logger.error(..., exc_info=True)` gets a beutilogs
report. Your log config, formatters, and levels are untouched.

```python
import logging
import sys

import beutilogs


class BeutilogsHandler(logging.Handler):
    def emit(self, record):
        if record.exc_info:
            exc = record.exc_info[1]
            sys.stderr.write(beutilogs.capture(exc, color=False) + "\n")


logging.getLogger("app").addHandler(BeutilogsHandler())
```

```python
log = logging.getLogger("app")

try:
    {}["missing"]
except KeyError:
    log.exception("job failed")     # normal logging + beutilogs report
```

Put `BeutilogsHandler()` in your `logging.dictConfig` handlers list if you
configure logging declaratively:

```python
"handlers": {
    "beutilogs": {"()": "myapp.logging_utils.BeutilogsHandler"},
}
```

---

## 5. Web frameworks

### Flask

A single error handler covers the whole app:

```python
from flask import Flask
import beutilogs

app = Flask(__name__)


@app.errorhandler(Exception)
def handle_error(exc):
    beutilogs.log(exc, path="logs/errors.jsonl")
    return {"error": type(exc).__name__}, 500
```

### FastAPI

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import beutilogs

app = FastAPI()


@app.exception_handler(Exception)
async def handle_error(request: Request, exc: Exception):
    beutilogs.log(exc, path="logs/errors.jsonl")
    return JSONResponse({"error": type(exc).__name__}, status_code=500)
```

> In production, do not put the exception message in the HTTP response —
> log it with beutilogs, return a generic message and a request id.

### Django

Middleware that logs and re-raises, so Django still renders its own 500 page:

```python
# myapp/middleware.py
import beutilogs


class BeutilogsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except Exception as exc:
            beutilogs.log(exc, path="logs/errors.jsonl")
            raise
```

```python
# settings.py
MIDDLEWARE = [
    # ...
    "myapp.middleware.BeutilogsMiddleware",
]
```

---

## 6. Async, threads, and background jobs

### `@beutilogs.watch`

Report an error from one function, then re-raise it unchanged. Works on sync
and `async def` functions:

```python
import beutilogs


@beutilogs.watch
def charge_card(order):
    ...


@beutilogs.watch
async def fetch_profile(user_id):
    ...
```

### Tasks and worker loops

`install()` covers `threading.Thread`. For a worker that must keep running,
catch, log, and continue:

```python
while True:
    job = queue.get()
    try:
        process(job)
    except Exception as exc:
        beutilogs.log(exc, path="errors.jsonl")   # log and carry on
        job.fail()
```

### asyncio event loop

`install()` does not see exceptions handed to the event loop's default
handler. Forward them yourself:

```python
import asyncio
import beutilogs


def handle_loop_exception(loop, context):
    exc = context.get("exception")
    if exc:
        beutilogs.report(exc)
    else:
        print("asyncio error:", context.get("message"))


loop = asyncio.new_event_loop()
loop.set_exception_handler(handle_loop_exception)
```

---

## 7. Keep errors as data (JSON lines)

`log()` appends one JSON object per line — one error per line, no parser
needed, greppable, and feedable to any log pipeline.

```json
{"time": "2025-01-01T00:00:00+00:00", "type": "KeyError", "message": "'c'",
 "where": {"file": "app.py", "line": 10, "function": "buggy", "source": "    return items[\"c\"]"},
 "recovered_line": null, "locals": {"items": "{'a': 1, 'b': 2}"}}
```

Read them back:

```console
$ tail -f errors.jsonl | jq -r '"\(.type) at \(.where.file):\(.where.line) - \(.message)"'
```

`where.line` is the line beutilogs believes raised. `recovered_line` is
non-null only when the reported line was empty and beutilogs had to recover the
real statement — that is a signal something upstream (a stale `.pyc`, a moved
file) is lying about line numbers.

### Rotate without a dependency

`log()` only appends, so any external rotation works. The cheapest option is to
roll the file at process start:

```python
import os

if os.path.exists("errors.jsonl") and os.path.getsize("errors.jsonl") > 5_000_000:
    os.replace("errors.jsonl", "errors.jsonl.1")
```

---

## 8. Test that your errors point at the right line

`capture()` returns a string, so an error message becomes an ordinary
assertion. This catches the exact failure beutilogs exists for: a report that
names a line with nothing on it.

```python
import beutilogs


def _divide(a, b):
    return a / b


def test_division_error_points_at_the_real_line():
    try:
        _divide(1, 0)
    except ZeroDivisionError as exc:
        text = beutilogs.capture(exc, color=False)
    assert "bug here" in text
    assert "return a / b" in text          # the real source, not a blank line
    assert "b = 0" in text                 # the locals that caused it
```

Run the package's own checks with no test framework:

```console
$ python tests/test_beutilogs.py
```

---

## 9. Production checklist

- **Do** call `beutilogs.install(path=...)` once, in the app entry point.
- **Do** use `capture(..., color=False)` when the output is a file, a log
  aggregator, or an HTTP response — colour codes are for terminals.
- **Do** keep the full exception chain: prefer `raise ... from exc`, beutilogs
  renders `cause`, `context`, and `ExceptionGroup` without extra work.
- **Don't** log secrets. `include_locals=True` is the default and locals can
  hold tokens or passwords — disable it for sensitive paths:

  ```python
  beutilogs.report(exc, include_locals=False)
  beutilogs.log(exc, path="errors.jsonl", include_locals=False)
  beutilogs.install(path="errors.jsonl", include_locals=False)
  ```

- **Don't** rely on the line number alone when the source is not shipped
  (frozen/zip apps). The report says `<source unavailable at ...>` instead of
  inventing a line — that is intentional, and `where.file` / `where.function`
  still identify the site.
- **Colour** is applied only to a real TTY and is disabled when `NO_COLOR` is
  set. Set `color=False` / `color=True` to force it.
- **Old consoles**: on a cp1252 Windows console the box art degrades to ASCII
  automatically, so reporting never raises `UnicodeEncodeError`.
