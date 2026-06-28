import sys
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import count
from math import sin
from threading import Event, Thread
from typing import TextIO

SPINNER_PALETTE = ("38;5;52", "38;5;88", "38;5;124", "38;5;160", "38;5;196", "38;5;203", "38;5;196", "38;5;160")


def ansi(text: str, code: str | int, stream: TextIO = sys.stdout) -> str:
    return f"\33[{code}m{text}\33[0m" if stream.isatty() else text


def spinner_line(
    text: str,
    index: int,
    size: int = 15,
    levels: str = "⣀⣤⣶⣷⣿",
    empty: str = " ",
    fixed: bool = False,
) -> str:
    start = max(0, (size - len(text)) // 2) if fixed else index % (size + len(text)) - len(text) + 1

    def cell(x: int) -> str:
        if start <= x < start + len(text):
            return f"\33[1;160m{text[x - start]}\33[0m"

        value = ((sin(index * 0.45 + x * 0.9) + sin(index * 0.25 - x * 1.4)) / 2 + 1) / 2
        char = (empty + levels)[round(value * len(levels))]
        code = 90 if char == empty else SPINNER_PALETTE[(x + index // 2) % len(SPINNER_PALETTE)]
        return f"\33[{code}m{char}\33[0m"

    return "  [" + "".join(cell(x) for x in range(size)) + "]"


def trace(text: str) -> None:
    print("\r\33[K" + ansi(f"# {text}", "94", sys.stderr), file=sys.stderr, flush=True)


def command_line(command: str, stream: TextIO = sys.stdout) -> str:
    return ansi(f"$ {command}", "92", stream)


def highlight_commands(text: str, stream: TextIO = sys.stdout) -> str:
    return "\n".join(command_line(line[2:], stream) if line.startswith("$ ") else line for line in text.splitlines())


def show_tool_result(result: str) -> None:
    if result.strip():
        print(highlight_commands(result.strip(), sys.stderr), file=sys.stderr)


def spinner(done: Event, text: str = "thinking", fixed: bool = False, status: str = "") -> None:
    if not sys.stderr.isatty():
        return

    for index in count():
        if done.wait(0.075):
            break

        suffix = " " + status if status else ""
        line = "\r" + spinner_line(text, index, fixed=fixed) + suffix + "\33[K"
        print(line, end="", file=sys.stderr, flush=True)

    print("\r\33[K", end="", file=sys.stderr, flush=True)


@contextmanager
def running_spinner(text: str, fixed: bool = False, status: str = "") -> Iterator[None]:
    done = Event()
    thread = Thread(target=spinner, args=(done, text), kwargs={"fixed": fixed, "status": status})
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()
