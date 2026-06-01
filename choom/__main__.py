import sys

from .agent import agentic_loop, collect_files, payload_text
from .config import Config, History, tokens
from .terminal import prompt_input

USAGE = "usage: python3 -m choom [prompt]\n\nRun without arguments to start the REPL.\n"


def repl(config: Config) -> None:
    print("choom repl. /exit to quit.")
    history: History = []

    while True:
        print()
        files = collect_files(config, trace_reads=False)
        config.current_tokens = tokens(payload_text(files, "", history))

        try:
            prompt = prompt_input(config).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if prompt in ("/exit", "/quit"):
            return
        if prompt:
            answer = agentic_loop(config, prompt, history)
            history.append((prompt, answer))
            if not config.streamed_output:
                print(answer)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("-h", "--help"):
        print(USAGE, end="")
        return 0

    config = Config()

    if args:
        answer = agentic_loop(config, " ".join(args))
        if not config.streamed_output:
            print(answer)
    else:
        repl(config)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
