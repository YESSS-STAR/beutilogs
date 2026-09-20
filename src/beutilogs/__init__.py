"""beutilogs - error logging that points at the line that actually broke.

Plain tracebacks regularly lie about *where* a bug is: the reported line is
blank, stale (an old ``linecache`` entry or a moved file), or buried in library
glue.  beutilogs rebuilds the report from the live frame:

* recomputes the real line from the frame's own bytecode (``co_lines``),
  so a stale line number cannot mislead you;
* re-reads the source from disk, and if the reported line is empty it recovers
  the real statement from the code object;
* shows the culprit frame, a caret under the failing statement, and locals;
* keeps the full exception cause/context chain and ``ExceptionGroup``s.

Public API: :func:`report`, :func:`capture`, :func:`log`, :func:`install`,
:func:`watch`.
"""

from __future__ import annotations

import json
import linecache
import os
import re
import reprlib
import sys
import threading
import traceback
from datetime import datetime, timezone

__version__ = "0.1.0"
__all__ = ["report", "capture", "log", "install", "watch"]

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_CYAN = "\x1b[36m"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_UNICODE_GLYPHS = {"tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│", "caret": "▲"}
_ASCII_GLYPHS = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|", "caret": "^"}


def _encodable(stream) -> bool:
    """Can this stream carry the box-drawing glyphs? (cp1252 consoles cannot.)"""
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return True  # e.g. io.StringIO - holds any str
    try:
        "╭─▲│╰╯".encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


# --------------------------------------------------------------------------- #
# source resolution - the part default logging gets wrong
# --------------------------------------------------------------------------- #
def _readline(filename: str, lineno: int) -> str:
    """Read a source line from disk, ignoring any stale linecache entry."""
    if not filename or filename.startswith("<"):
        return ""
    linecache.checkcache(filename)
    line = linecache.getline(filename, lineno)
    if line:
        return line.rstrip("\n")
    try:
        with open(filename, "r", encoding="utf-8", errors="replace") as fh:
            for i, text in enumerate(fh, 1):
                if i == lineno:
                    return text.rstrip("\n")
                if i > lineno:
                    break
    except OSError:
        pass
    return ""


def _readlines(filename: str):
    """Whole-file read, used by the recovery scan (one open instead of N)."""
    if not filename or filename.startswith("<"):
        return []
    try:
        with open(filename, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def _true_lineno(frame, tb_lineno: int) -> int:
    """Best-effort line from the frame's bytecode.

    Only used when the traceback has no usable line number: a frame that is
    still live (an exception caught and re-logged) has ``f_lasti`` pointing at
    whatever it is executing *now*, not at the raise site, so ``tb_lineno`` wins
    whenever it exists.
    """
    code = frame.f_code
    lasti = getattr(frame, "f_lasti", -1)
    lines = getattr(code, "co_lines", None)
    if lines and lasti >= 0:
        for start, end, lineno in code.co_lines():
            if lineno and start <= lasti < end:
                return lineno
    return tb_lineno


def _locate(frame, tb_lineno):
    """Return ``(source_line, real_lineno, recovered)`` for a traceback frame."""
    lineno = tb_lineno or _true_lineno(frame, tb_lineno)
    line = _readline(frame.f_code.co_filename, lineno)
    if line.strip() and not line.lstrip().startswith("#"):
        return line, lineno, False

    # The reported line has nothing on it (or is a comment).  Recover the real
    # statement using the code object's own line table.  Read the file once:
    # candidates can be numerous and re-opening per candidate is quadratic.
    source_lines = _readlines(frame.f_code.co_filename)
    lines = getattr(frame.f_code, "co_lines", None)
    candidates = sorted({ln for _, _, ln in lines() if ln}) if lines else []
    for cand in sorted(candidates, key=lambda ln: abs(ln - lineno)):
        text = _readline(frame.f_code.co_filename, cand)
        if not text and 0 < cand <= len(source_lines):
            text = source_lines[cand - 1]
        if text.strip() and not text.lstrip().startswith("#"):
            return text, cand, True
    return "", lineno, False


def _culprit(exc: BaseException):
    """Pick the frame that actually failed, ignoring beutilogs itself.

    Returns ``(frame, lineno, frame_count)``.
    """
    tb = exc.__traceback__
    if tb is None:
        return None, None, 0
    frames = list(traceback.walk_tb(tb))
    if not frames:
        return None, None, 0

    def internal(frame) -> bool:
        filename = frame.f_code.co_filename
        return filename.startswith("<frozen") or os.path.abspath(filename).startswith(_PKG_DIR)

    user_frames = [item for item in frames if not internal(item[0])]
    frame, lineno = (user_frames or frames)[-1]
    return frame, lineno, len(frames)


_REPR_LIMIT = 64


def _safe_repr(value) -> str:
    """``repr`` bounded for big containers.

    A million-element list should cost microseconds, not build a megabyte
    string only to be truncated to 240 characters.
    """
    try:
        huge = len(value) > _REPR_LIMIT
    except TypeError:
        huge = False
    return reprlib.repr(value) if huge else repr(value)


def _locals(frame, limit: int = 12, maxlen: int = 240):
    out = []
    for name, value in frame.f_locals.items():
        try:
            rep = _safe_repr(value)
        except Exception:
            rep = "<unrepr-able>"
        if len(rep) > maxlen:
            rep = rep[:maxlen] + "..."
        out.append(f"{name} = {rep}")
        if len(out) >= limit:
            out.append("...")
            break
    return out


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _width(text: str) -> int:
    return len(_ANSI.sub("", text))


def _box(title: str, lines, color: bool, chars: dict) -> str:
    width = max([_width(title) + 1] + [_width(l) for l in lines] + [0])
    head = chars["h"] * (width - len(title) - 1)
    top = (
        f"{_DIM}{chars['tl']}{chars['h']} {_RESET}{_BOLD}{title}{_RESET}{_DIM} {head}{chars['tr']}{_RESET}"
        if color
        else f"{chars['tl']}{chars['h']} {title} {head}{chars['tr']}"
    )
    body = []
    for line in lines:
        pad = " " * (width - _width(line))
        if color:
            body.append(f"{_DIM}{chars['v']}{_RESET} {line}{pad} {_DIM}{chars['v']}{_RESET}")
        else:
            body.append(f"{chars['v']} {line}{pad} {chars['v']}")
    bottom = (
        f"{_DIM}{chars['bl']}{chars['h'] * (width + 2)}{chars['br']}{_RESET}"
        if color
        else f"{chars['bl']}{chars['h'] * (width + 2)}{chars['br']}"
    )
    return "\n".join([top, *body, bottom])


def _colors(enabled: bool) -> dict:
    if not enabled:
        return {k: "" for k in ("reset", "bold", "dim", "red", "yellow", "cyan")}
    return {
        "reset": _RESET,
        "bold": _BOLD,
        "dim": _DIM,
        "red": _RED,
        "yellow": _YELLOW,
        "cyan": _CYAN,
    }


def _tty(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def capture(exc: BaseException | None = None, *, include_locals: bool = True,
            color: bool | None = None, unicode: bool = True) -> str:
    """Return a formatted, human-readable report for ``exc`` (default: the
    exception currently being handled)."""
    if exc is None:
        exc = sys.exc_info()[1]
    if exc is None:
        return "beutilogs: no active exception to report"

    c = _colors(bool(color))
    chars = _UNICODE_GLYPHS if unicode else _ASCII_GLYPHS
    etype = type(exc).__name__
    message = str(exc)

    frame, tb_lineno, frame_count = _culprit(exc)
    header = [f"{c['bold']}{c['red']}{etype}{c['reset']}"]
    if message:
        header.append(message)

    lines = list(header)
    recovered_from = None
    real = None
    if frame is not None:
        reported = tb_lineno or _true_lineno(frame, tb_lineno)
        source, real, recovered = _locate(frame, tb_lineno)
        if recovered and real != reported:
            recovered_from = reported
        path = frame.f_code.co_filename
        rel = os.path.relpath(path) if os.path.isabs(path) else path
        where = f"{c['yellow']}{rel}:{real}{c['reset']} in {frame.f_code.co_name}()"
        lines.append(f"{c['bold']}bug here ->{c['reset']} {where}")
    lines_out = [_box(f"beutilogs | {etype}", lines, bool(color), chars)]

    if frame is not None:
        if recovered_from is not None:
            lines_out.append(
                f"{c['dim']}note: line {recovered_from} was empty; recovered the real "
                f"statement at line {real}{c['reset']}"
            )
        if source:
            caret_col = len(source) - len(source.lstrip())
            gutter = f"{real:>4} | "
            lines_out.append(f"{c['dim']}{gutter}{c['reset']}{source}")
            lines_out.append(
                f"{c['dim']}{' ' * len(gutter)}{c['reset']}{' ' * caret_col}{c['cyan']}{chars['caret']}{c['reset']}"
            )
        else:
            lines_out.append(f"{c['dim']}<source unavailable at {frame.f_code.co_filename}:{real}>{c['reset']}")
        if include_locals:
            shown = _locals(frame)
            if shown:
                lines_out.append(f"{c['dim']}locals:{c['reset']}")
                lines_out.extend(f"  {c['dim']}{l}{c['reset']}" for l in shown)

    # Rendering the chain is the single most expensive thing here, and for a
    # lone exception with one frame it would only repeat the block above.
    has_chain = (
        exc.__cause__ is not None
        or (exc.__context__ is not None and not exc.__suppress_context__)
        or isinstance(exc, BaseExceptionGroup)
    )
    if frame_count > 1 or has_chain:
        body = "".join(traceback.TracebackException.from_exception(exc).format())
        lines_out.append(f"{c['dim']}traceback (full chain):{c['reset']}")
        lines_out.append(body.rstrip("\n"))
    return "\n".join(lines_out)


def report(exc: BaseException | None = None, *, stream=None, include_locals: bool = True,
           color: bool | None = None, unicode: bool | None = None) -> str:
    """Print a report to ``stream`` (default ``stderr``) and return it.

    Falls back to ASCII box characters when the stream's encoding cannot carry
    the Unicode ones (e.g. a cp1252 Windows console), so reporting never raises.
    """
    stream = stream if stream is not None else sys.stderr
    if unicode is None:
        unicode = _encodable(stream)
    text = capture(
        exc,
        include_locals=include_locals,
        color=_tty(stream) if color is None else color,
        unicode=unicode,
    )
    print(text, file=stream)
    return text


def log(exc: BaseException | None = None, path: str = "beutilogs.jsonl",
        *, include_locals: bool = True, stream=None) -> dict:
    """Write a structured JSON record to ``path`` and print the report.

    Returns the record. One JSON object per line, so it is directly greppable
    and feedable to log tooling.
    """
    if exc is None:
        exc = sys.exc_info()[1]
    if exc is None:
        raise RuntimeError("beutilogs.log() called with no active exception")

    frame, tb_lineno, _ = _culprit(exc)
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "type": type(exc).__name__,
        "message": str(exc),
        "where": None,
        "recovered_line": None,
        "locals": {},
    }
    if frame is not None:
        reported = tb_lineno or _true_lineno(frame, tb_lineno)
        source, real, recovered = _locate(frame, tb_lineno)
        record["where"] = {
            "file": frame.f_code.co_filename,
            "line": real,
            "function": frame.f_code.co_name,
            "source": source,
        }
        record["recovered_line"] = reported if recovered and real != reported else None
        if include_locals:
            for name, value in list(frame.f_locals.items())[:12]:
                try:
                    record["locals"][name] = _safe_repr(value)[:240]
                except Exception:
                    record["locals"][name] = "<unrepr-able>"

    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    report(exc, stream=stream, include_locals=include_locals)
    return record


def install(*, stream=None, path: str | None = None, include_locals: bool = True):
    """Route uncaught exceptions (main thread and threads) through beutilogs.

    Pass ``path`` to also append a JSON record per error.  Returns an
    ``uninstall`` callable that restores the previous hooks.
    """
    old_hook = sys.excepthook
    old_thread_hook = threading.excepthook

    def emit(exc):
        if path:
            log(exc, path, include_locals=include_locals, stream=stream)
        else:
            report(exc, stream=stream, include_locals=include_locals)

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            old_hook(exc_type, exc, tb)
            return
        emit(exc)

    def thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            old_thread_hook(args)
            return
        emit(args.exc_value)

    sys.excepthook = hook
    threading.excepthook = thread_hook

    def uninstall():
        sys.excepthook = old_hook
        threading.excepthook = old_thread_hook

    return uninstall


def watch(func):
    """Decorator: report the error, then re-raise it unchanged.

    Works on sync and ``async def`` functions.
    """
    import functools
    import inspect

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - reporting is the point
                report(exc)
                raise

        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - reporting is the point
            report(exc)
            raise

    return wrapper
