# choom.py

A tiny zero-dependency coding agent in one Python file.

WARNING: this was created for educational purposes, not meant for actual use. Agent can run shell commands and has no sandbox.

Run:

```sh
OPENROUTER_API_KEY="..." MODEL="minimax/minimax-m2.7" python3 choom.py
```

Use `AGENT_APPROVE=all` to run shell commands without asking.

Inspect sessions:

```sh
cat agent_sessions.json
```

It logs prompts, exact API request payloads, responses, actions, tool results, and final answers.

## Step-by-step build

1. Add `Config`
   Keep all runtime settings in one dataclass: API key, model, working dir, approval mode, token limits.

2. Add the spinner
   Show activity states in the terminal: `prompt`, `thinking`, and `running`.

3. Add local context
   `collect_files()` loads `README.md`, `AGENTS.md`, and `.agents/skills/**/SKILL.md`.

4. Build the system prompt
   Include cwd, OS, Python version, shell, and paths to discovered context files.

5. Add shell execution
   `execute_shell()` runs commands with `subprocess.run`, cwd, timeout, approval, and output truncation.

6. Add the model call
   `call_model()` sends a minimal Responses API request with `urllib.request`.

7. Add the action parser
   The model returns either `{"cmd":"..."}` or `{"final":"..."}`.

8. Add the agent loop
   Call the model, run requested commands, append results, repeat until final or limits.

9. Add the REPL
   Start with `python3 choom.py`, type a task, exit with `/exit`.

10. Add session logs
    Write the full run to `./agent_sessions.json` for debugging and replay.
