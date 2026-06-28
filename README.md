# choom.py

A tiny coding agent with zero runtime dependencies.
Development checks use Ruff and mypy via `uv`.

WARNING: this was created for educational purposes, not meant for actual use. Agent can run shell commands and has no sandbox.

## Approach

- Single invocation, not interactive: prompt in, answer out, no REPL scaffolding. The agent may still loop internally on tool calls until it reaches a final answer.
- Per-command approval kept simple (yes/no); there is no auto-approve mode.

Run:

```sh
OPENROUTER_API_KEY="..." MODEL="minimax/minimax-m2.7" python3 -m choom "your prompt"
```

Use `AGENT_STREAM=1` to show generated text as it arrives.
Use `AGENT_REASONING=auto` to request and show reasoning summaries when the model supports them.
Shell commands require per-command approval (`y`/`n`); there is no auto-approve mode.

With local llama.cpp:

```sh
URL="http://127.0.0.1:8080/v1/chat/completions" MODEL="gemma-4-12b" python3 -m choom "your prompt"
```

Inspect sessions:

```sh
cat agent_sessions.json
```

It logs prompts, exact API request payloads, responses, tool results, errors, and final answers.

Checks:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run --script scripts/check_source.py [paths...]
```

Override source limits with `SOURCE_LINE_LIMIT` and `SOURCE_COMPLEXITY_LIMIT`.

## Step-by-step build

1. Add `Config`
   Keep all runtime settings in one dataclass: API key, model, working dir, token limits.

2. Add the spinner
   Show activity states in the terminal.

3. Add local context
   `collect_files()` loads `README.md`, `AGENTS.md`, and `.agents/skills/**/SKILL.md`.

4. Build the system prompt
   Include cwd, OS, Python version, shell, and paths to discovered context files.

5. Add shell execution
   `execute_shell()` runs commands with approval, cwd validation, timeout, visible output, and output truncation.

6. Add the model call
   `call_model()` sends a minimal Responses API request with `urllib.request`.

7. Add function tools
   The model can call `execute_shell` with `command`, `description`, `cwd`, `timeout`, and `env`.

8. Add the agent loop
   Call the model, run requested tool calls, append results, repeat until final or limits.

9. Add session logs
   Write the full run to `./agent_sessions.json` for debugging.

10. Add live trace
    Print reads, API calls, and shell commands as they happen.
