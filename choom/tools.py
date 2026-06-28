import json
import os
import subprocess
import sys
from typing import Any

from .config import Config
from .terminal import command_line, running_spinner, show_tool_result, trace

ToolCall = dict[str, Any]


def tool_schema() -> dict[str, Any]:
    props = {
        "command": {"type": "string"},
        "description": {"type": "string"},
        "cwd": {"type": ["string", "null"]},
        "timeout": {"type": "integer"},
        "env": {"type": "object", "additionalProperties": {"type": "string"}},
    }
    return {
        "type": "function",
        "name": "execute_shell",
        "description": "Run a shell command.",
        "parameters": {
            "type": "object",
            "properties": props,
            "required": ["command", "description"],
            "additionalProperties": False,
        },
    }


TOOL = tool_schema()


def approve(config: Config, args: dict[str, Any]) -> bool:
    print(f"\n# {args.get('description', 'run command')}", file=sys.stderr)
    print(command_line(str(args.get("command", "")), sys.stderr), file=sys.stderr)

    try:
        choice = input("Approve? [y] yes  [n] no: ").strip().lower()
    except EOFError:
        return False

    return choice in ("y", "yes")


def safe_cwd(config: Config, cwd: str | None) -> tuple[str, str]:
    root = os.path.abspath(config.working_dir)
    path = os.path.abspath(cwd or root)

    if not os.path.isdir(path):
        return root, f"ignored invalid cwd: {path}\n"
    if os.path.commonpath([root, path]) != root:
        return root, f"ignored outside cwd: {path}\n"
    return path, ""


def execute_shell(
    config: Config,
    command: str,
    description: str = "",
    cwd: str | None = None,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> str:
    args = {
        "command": command,
        "description": description,
        "cwd": cwd,
        "timeout": timeout,
        "env": env,
    }
    if not approve(config, args):
        return "DENIED: user denied command"

    try:
        with running_spinner("running", fixed=True):
            if config.verbose:
                trace(f"run {command}")
            run_env = {**os.environ, **(env or {})}
            run_cwd, cwd_note = safe_cwd(config, cwd)
            process = subprocess.run(
                command,
                shell=True,
                cwd=run_cwd,
                env=run_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
            result = f"$ {command}\n{cwd_note}exit {process.returncode}\n{process.stdout}"
            return result[-config.max_tool_chars :]
    except (EOFError, OSError, subprocess.TimeoutExpired) as error:
        return f"{type(error).__name__}: {error}"[-config.max_tool_chars :]


def tool_stdout(result: str) -> str:
    out = []
    after_status = False

    for line in result.strip().splitlines():
        if line.startswith("$ "):
            after_status = False
        elif line.startswith("exit "):
            after_status = True
        elif after_status:
            out.append(line)

    return "\n".join(out)


def tool_summary(result: str) -> str:
    return ("done" if quiet_success(result) else result.strip()) or "done"


def quiet_success(result: str) -> bool:
    statuses = [line for line in result.strip().splitlines() if line.startswith("exit ")]
    return bool(statuses) and all(status == "exit 0" for status in statuses) and not tool_stdout(result).strip()


def denied(result: str) -> bool:
    return any(line.startswith("DENIED:") for line in result.splitlines())


def tool_output(config: Config, call: ToolCall) -> dict[str, str]:
    try:
        args = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError as error:
        result = f"bad arguments: {error}"
    else:
        result = execute_tool(config, call, args)

    if config.verbose:
        show_tool_result(result)
    return {"type": "function_call_output", "call_id": str(call["call_id"]), "output": result}


def execute_tool(config: Config, call: ToolCall, args: object) -> str:
    if call.get("name") != "execute_shell":
        return "unknown tool"

    match args:
        case {"command": str(command), **rest}:
            description = rest.pop("description", "")
            cwd = rest.pop("cwd", None)
            timeout = rest.pop("timeout", 120)
            env = rest.pop("env", None)
        case _:
            return "bad arguments: missing string command"

    match description, cwd, timeout, env, rest:
        case str(), (str() | None), int(), (dict() | None), {} if valid_env(env):
            return execute_shell(config, command, description, cwd, timeout, env)
        case _:
            return "bad arguments: expected command, description, cwd, timeout, env"


def valid_env(env: object) -> bool:
    return env is None or (
        isinstance(env, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
    )


def call_command(call: ToolCall) -> str:
    try:
        args = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        return ""

    command = args.get("command", "") if isinstance(args, dict) else ""
    return command if isinstance(command, str) else ""
