"""Minimal runnable checks: ``python tests/test_beutilogs.py`` or ``pytest``."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import beutilogs  # noqa: E402


def _boom():
    numbers = [1, 2, 3]
    return numbers[99]  # IndexError raised here


def test_capture_names_the_real_line():
    try:
        _boom()
    except Exception as exc:
        text = beutilogs.capture(exc, color=False)
    assert "IndexError" in text
    assert "list index out of range" in text
    assert "bug here" in text
    assert "numbers[99]" in text          # the real source line, not a blank one
    assert "numbers = [1, 2, 3]" in text  # locals from the culprit frame
    assert "traceback (full chain)" in text


def test_blank_reported_line_is_recovered():
    """If the reported line is empty, find the real statement from the code table."""
    class FakeCode:
        co_filename = "moved.py"
        co_name = "f"

        def co_lines(self):
            return [(0, 2, 10), (2, 4, 11)]

    class FakeFrame:
        f_code = FakeCode()
        f_lasti = 0

    original = beutilogs._readline
    beutilogs._readline = lambda filename, lineno: "" if lineno == 10 else "recovered statement"
    try:
        source, real, recovered = beutilogs._resolve_source(FakeFrame(), 10)
    finally:
        beutilogs._readline = original
    assert recovered is True
    assert real == 11
    assert source == "recovered statement"


def test_log_writes_jsonl():
    record = None
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "errors.jsonl")
        try:
            _boom()
        except Exception as exc:
            record = beutilogs.log(exc, path, stream=open(os.devnull, "w"))
        with open(path, encoding="utf-8") as fh:
            line = json.loads(fh.readline())
    assert record["type"] == "IndexError"
    assert line["where"]["function"] == "_boom"
    assert "numbers[99]" in line["where"]["source"]
    assert line["locals"]["numbers"] == "[1, 2, 3]"


def test_live_frame_reports_raise_site_not_except_site():
    """Caught-and-re-logged errors must blame the raise line, not the handler."""
    try:
        raise ValueError("boom")
    except ValueError as exc:
        expected = exc.__traceback__.tb_lineno  # the `raise` line, in this frame
        text = beutilogs.capture(exc, color=False)  # frame is still live here
    assert f"test_beutilogs.py:{expected} in test_live_frame_reports_raise_site_not_except_site()" in text
    assert 'raise ValueError("boom")' in text


def test_missing_source_is_labelled_not_faked():
    code = compile("raise ValueError('gone')", "<virtual>", "exec")
    try:
        exec(code, {})
    except Exception as exc:
        text = beutilogs.capture(exc, color=False)
    assert "source unavailable" in text


def test_watch_reports_and_reraises():
    import io

    @beutilogs.watch
    def fail():
        raise ValueError("watched")

    stream = io.StringIO()
    original, sys.stderr = sys.stderr, stream
    try:
        try:
            fail()
        except ValueError:
            pass
    finally:
        sys.stderr = original
    assert "ValueError" in stream.getvalue()
    assert "watched" in stream.getvalue()


def test_watch_supports_async_functions():
    import asyncio
    import io

    @beutilogs.watch
    async def fail():
        raise ValueError("async watched")

    stream = io.StringIO()
    original, sys.stderr = sys.stderr, stream
    try:
        try:
            asyncio.run(fail())
        except ValueError:
            pass
    finally:
        sys.stderr = original
    assert "async watched" in stream.getvalue()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
