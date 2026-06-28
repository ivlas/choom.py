import sys

from .agent import agentic_loop
from .config import Config

USAGE = "usage: python3 -m choom <prompt>\n       uv run python -m choom <prompt>\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help"):
        print(USAGE, end="")
        return 0

    config = Config()
    answer = agentic_loop(config, " ".join(args))
    if not config.streamed_output:
        print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
