# beutilogs

> **Error logging that points at the line that actually broke — not a blank one.**

[![PyPI](https://img.shields.io/badge/pypi-beutilogs-blue)](https://pypi.org/project/beutilogs/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)]()

Python's default traceback is usually right, and occasionally *confidently wrong*.
You get `File "app.py", line 42` — you open line 42 and there is **nothing there**:
a blank line, a comment, or code that has moved since the process started. You then
burn ten minutes finding the real bug.

`beutilogs` refuses to do that. It rebuilds the error report from the **live frame**,
so it can only show you a line that really exists.

```console
$ pip install beutilogs
$ python -m beutilogs      # see the before/after for yourself
```

---

## What the default logger shows

```python
def buggy():
    items = {"a": 1, "b": 2}
    return items["c"]
```

```text
Traceback (most recent call last):
  File "app.py", line 15, in main
    buggy()
  File "app.py", line 10, in buggy
    return items["c"]
KeyError: 'c'
```

Correct — but you still have to *read* four lines, find the deepest one, and guess
which local mattered. And under a stale `.pyc`, a moved file, or a bundled app, that
`line 10` may point at an empty line.

## What beutilogs shows

```console
╭─ beutilogs | KeyError ──────────────────────────────╮
│ KeyError                                            │
│ 'c'                                                 │
│ bug here -> src\beutilogs\__main__.py:10 in buggy() │
╰─────────────────────────────────────────────────────╯
  10 |     return items["c"]  # KeyError raised right here
           ▲
locals:
  items = {'a': 1, 'b': 2}
traceback (full chain):
Traceback (most recent call last):
  File "src\beutilogs\__main__.py", line 15, in main
    buggy()
  File "src\beutilogs\__main__.py", line 10, in buggy
    return items["c"]
KeyError: 'c'
```

One glance: **what** failed, **where** (the real line, with a caret), **why**
(the locals at that frame), and the **full chain** if there was one.

---

## Why the line number can be wrong — and how beutilogs fixes it

| Default traceback | beutilogs |
| --- | --- |
| Trusts `tb_lineno` and `linecache` | Recomputes the line from the frame's own bytecode (`code.co_lines()` + `f_lasti`), so a stale cache cannot mislead it |
| Prints an empty line if the source moved | Re-reads the source from disk, bypassing `linecache`, and recovers the real statement from the code object — flagging it as recovered |
| Shows a blurry wall of frames | Names one **culprit** frame (`bug here ->`), skipping library/`<frozen>` internals |
| Leaves you to scroll and `print()` | Shows the culprit's **locals** inline |
| Drops chain context in custom handlers | Keeps `raise ... from ...`, `__context__`, and `ExceptionGroup`s intact |
| Advisory only | Can write structured **JSON lines** for real log pipelines |

---

## Usage

### Log any exception

```python
import beutilogs

try:
    run_job()
except Exception as exc:
    beutilogs.log(exc, path="errors.jsonl")   # pretty report + one JSON line
```

`beutilogs.report(exc)` prints the pretty report only.
`beutilogs.capture(exc)` returns it as a string (handy for tests and web responses).

### Stop losing uncaught errors

```python
import beutilogs

beutilogs.install(path="errors.jsonl")   # sys.excepthook + threading.excepthook
```

Every uncaught exception — in the main thread or a worker thread — now goes
through beutilogs. `KeyboardInterrupt` and `SystemExit` keep their normal behaviour.
`install()` returns an `uninstall()` callable.

### Watch one function

```python
@beutilogs.watch
def charge_card(order):
    ...
```

Reports the error, then re-raises it unchanged.

---

## API

| Function | Purpose |
| --- | --- |
| `capture(exc=None, *, include_locals=True, color=None, unicode=True) -> str` | Formatted report as a string |
| `report(exc=None, *, stream=None, include_locals=True, color=None, unicode=None) -> str` | Print the report (default `stderr`) and return it |
| `log(exc=None, path="beutilogs.jsonl", *, include_locals=True, stream=None) -> dict` | Print + append a JSON record; returns the record |
| `install(*, stream=None, path=None, include_locals=True) -> callable` | Hook uncaught exceptions; returns `uninstall` |
| `watch(func)` | Decorator: report, then re-raise |

Details that matter:

- **No dependencies.** Standard library only.
- **Never crashes while reporting.** If the output stream is a cp1252 console, it
  degrades to ASCII box characters instead of raising `UnicodeEncodeError`.
- **Respects `NO_COLOR`** and only colours a real TTY.
- `exc=None` means "the exception currently being handled" (`sys.exc_info()`).

### The JSON record

```json
{
  "time": "2025-01-01T00:00:00+00:00",
  "type": "KeyError",
  "message": "'c'",
  "where": {"file": "app.py", "line": 10, "function": "buggy", "source": "    return items[\"c\"]"},
  "recovered_line": null,
  "locals": {"items": "{'a': 1, 'b': 2}"}
}
```

`recovered_line` is set when the reported line was empty and beutilogs corrected it.

---

## Tests

No framework required:

```console
$ python tests/test_beutilogs.py
ok  test_blank_reported_line_is_recovered
ok  test_capture_names_the_real_line
ok  test_log_writes_jsonl
ok  test_missing_source_is_labelled_not_faked
4 passed
```

## License

MIT — see [LICENSE](LICENSE).
