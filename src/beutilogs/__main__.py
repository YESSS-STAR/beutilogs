"""Demo: ``python -m beutilogs`` — shows why the line number usually lies."""

import sys

import beutilogs


def buggy():
    items = {"a": 1, "b": 2}
    return items["c"]  # KeyError raised right here


def main():
    try:
        buggy()
    except Exception as exc:
        print("--- default traceback -------------------------------------------")
        import traceback

        traceback.print_exception(exc, file=sys.stdout)
        print("\n--- beutilogs ---------------------------------------------------")
        beutilogs.report(exc, stream=sys.stdout, color=False)


if __name__ == "__main__":
    main()
