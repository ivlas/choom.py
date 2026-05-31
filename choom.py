import sys, os, threading, time
from dataclasses import dataclass, field
from itertools import count
from math import sin
from pathlib import Path

def _ignore() -> frozenset[str]:
    text = Path(".gitignore").read_text() if Path(".gitignore").exists() else ""
    return frozenset({p for x in text.splitlines() if (p := x.strip().strip("/")) and not p.startswith(("#", "!"))} | {".git"})

@dataclass(frozen=True)
class Config:
    url: str = os.getenv("URL", "https://openrouter.ai/api/v1/responses")
    model: str = os.getenv("MODEL", "openai/gpt-5.5")
    steps_limit: int = int(os.getenv("AGENT_STEPS_LIMIT", "200"))
    approve_mode: bool = os.getenv("AGENT_APPROVE", "").lower() == "all"
    sessions: str = os.path.expanduser(os.getenv("AGENT_SESSIONS", "~/.agent_sessions.json"))
    working_dir: str = field(default_factory=os.getcwd)
    ignore: frozenset[str] = field(default_factory=_ignore)

def spinner(done, text="thinking", size=15, levels="⣀⣤⣶⣷⣿", empty=" "):
    if not sys.stderr.isatty(): return
    palette = "38;5;52 38;5;88 38;5;124 38;5;160 38;5;196 38;5;203 38;5;196 38;5;160".split()
    color = lambda c, s: f"\33[{c}m{s}\33[0m"

    for i in count():
        if done.wait(0.075): break
        limit = size - len(text)
        p = i % (limit * 2) if limit > 0 else 0
        start = 0 if limit <= 0 else min(p, limit * 2 - p)

        def cell(x):
            if start <= x < start + len(text): return color("1;160", text[x - start])
            v = ((sin(i * .45 + x * .9) + sin(i * .25 - x * 1.4)) / 2 + 1) / 2
            ch = (empty + levels)[round(v * len(levels))]
            return color(90 if ch == empty else palette[(x + i // 2) % len(palette)], ch)

        print("\r  [" + "".join(cell(x) for x in range(size)) + "]\33[K", end="", file=sys.stderr, flush=True)
    print("\r\33[K", end="", file=sys.stderr, flush=True)


if __name__ == "__main__":
    # done = threading.Event()
    # (spinner := threading.Thread(target=spinner, args=(done,))).start()
    # try:
    #     while True: time.sleep(1)
    # finally:
    #     done.set(); spinner.join()
    config = Config()
    print(config)
