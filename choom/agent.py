import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import Config, History, tokens
from .terminal import highlight_commands, running_spinner, trace
from .tools import (
    TOOL,
    ToolCall,
    call_command,
    denied,
    execute_shell,
    quiet_success,
    tool_output,
    tool_summary,
)

Response = dict[str, Any]
ToolPayload = list[dict[str, str]]


def collect_files(config: Config, trace_reads: bool = True) -> dict[str, str]:
    root = Path(config.working_dir)
    paths = [root / "README.md", root / "AGENTS.md"]
    skills = root / ".agents/skills"

    if skills.exists():
        paths += sorted(skills.rglob("SKILL.md"))

    files = {str(path.resolve()): path.read_text() for path in paths if path.exists()}
    if trace_reads:
        for path in files:
            trace(f"read {path}")
    return files


def build_system_prompt(config: Config, files: dict[str, str] | None = None) -> str:
    files = files or collect_files(config)

    def find(suffix: str) -> str:
        return ", ".join(path for path in files if path.endswith(suffix)) or f"no {suffix} defined"

    shell = os.getenv("SHELL") or os.getenv("COMSPEC", "?")
    env = f"cwd={config.working_dir} os={sys.platform} py={sys.version.split()[0]} shell={shell}"
    ctx = f"readme={find('README.md')} agents={find('AGENTS.md')} skills={find('SKILL.md')}"
    role = "You are a tiny shell coding agent. Use execute_shell to inspect/edit/test. Stay inside cwd. Be concise."
    return f"{role}\nenvironment: {env}\ncontext: {ctx}"


def log_event(config: Config, sid: str, event: str, data: object) -> None:
    path = Path(config.working_dir) / Path(config.sessions).name
    log = json.loads(path.read_text()) if path.exists() else []
    log.append({"time": time.time(), "session": sid, "event": event, "data": data})
    path.write_text(json.dumps(log, indent=2))


def prompt_action(prompt: str) -> dict[str, str] | None:
    text = prompt.strip()
    if text.startswith("run "):
        return {"cmd": text[4:]}
    if text.startswith(("cat ", "ls ", "pwd", "find ", "rg ", "sed ")):
        return {"cmd": text}

    if "agent_sessions.json" in text and any(word in text.lower() for word in ("read", "content", "show")):
        return {"cmd": "cat agent_sessions.json"}

    return None


def call_model(
    config: Config,
    instructions: str,
    payload: object,
    sid: str,
    used: int,
    previous: str | None = None,
) -> Response:
    if not config.api_key:
        raise SystemExit("set OPENROUTER_API_KEY")

    body: dict[str, object] = {
        "model": config.model,
        "instructions": instructions,
        "tools": [TOOL],
        "input": payload,
        "max_output_tokens": config.max_output_tokens,
    }
    if previous:
        body["previous_response_id"] = previous

    log_event(config, sid, "api_request", body)
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(config.url, json.dumps(body).encode(), headers)
    trace(f"api {config.model} [{used} / {config.session_token_limit}]")

    status = f"[{used} / {config.session_token_limit}]"
    try:
        with running_spinner("thinking", status=status), urllib.request.urlopen(request) as response:
            data = json.load(response)
        if not isinstance(data, dict):
            return {"output_text": "api error: response was not an object"}
        log_event(config, sid, "api_response", data)
        return data
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        message = f"HTTP {error.code}: {detail or error.reason}"
        log_event(config, sid, "api_error", message)
        return {"output_text": f"api error: {message}"}
    except Exception as error:
        log_event(config, sid, "api_error", str(error))
        raise


def response_text(response: Response) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str):
        return output_text

    return "".join(
        str(part.get("text", ""))
        for item in response.get("output", [])
        if isinstance(item, dict) and item.get("type") == "message"
        for part in item.get("content", [])
        if isinstance(part, dict) and part.get("type") == "output_text"
    )


def useful_text(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and any(char.isalnum() for char in stripped)


def multi_step_prompt(prompt: str) -> bool:
    text = " " + prompt.lower() + " "
    return any(mark in text for mark in (" then ", " and ", ",", ";", " after ", " also ", "\n"))


def history_text(history: History) -> str:
    return "\n\n".join(f"User: {prompt}\nAssistant: {answer}" for prompt, answer in history)


def payload_text(files: dict[str, str], prompt: str, history: History | None = None) -> str:
    past = f"\n\nPrevious conversation:\n{history_text(history)}" if history else ""
    context = "\n\n".join(f"# {path}\n{text}" for path, text in files.items())
    return context + past + f"\n\nUser: {prompt}"


def followup_payload(
    files: dict[str, str],
    prompt: str,
    tool_results: list[str],
    history: History | None = None,
) -> str:
    results = "\n\n".join(f"Tool result {index}:\n{result}" for index, result in enumerate(tool_results, 1))
    instruction = (
        "Continue from these results. Do not repeat a command that already succeeded unless the "
        "user explicitly asked to verify. If the task is complete, answer briefly. If more work "
        "is needed, call execute_shell again."
    )
    return payload_text(files, prompt, history) + f"\n\nTool results so far:\n{results}\n\n{instruction}"


def function_calls(response: Response) -> list[ToolCall]:
    return [
        item for item in response.get("output", []) if isinstance(item, dict) and item.get("type") == "function_call"
    ]


def run_direct_action(config: Config, sid: str, command: str) -> str:
    result = execute_shell(config, command, "user requested direct shell command")
    log_event(config, sid, "tool_result", result)
    return highlight_commands(result.strip(), sys.stdout)


def final_answer(config: Config, sid: str, response: Response, last_tool: str) -> str:
    answer = response_text(response)
    if not useful_text(answer):
        answer = tool_summary(last_tool)
    log_event(config, sid, "final", answer)
    return highlight_commands(answer, sys.stdout)


def run_tool_calls(config: Config, sid: str, calls: list[ToolCall]) -> tuple[ToolPayload, str]:
    payload = [tool_output(config, call) for call in calls]
    last_tool = "\n".join(item["output"] for item in payload)
    log_event(config, sid, "tool_result", payload)
    return payload, last_tool


def repeated_command(requested: list[str], commands: list[str]) -> str:
    return next((command for command in requested if command and command in commands), "")


def remember_commands(commands: list[str], requested: list[str]) -> None:
    commands.extend(command for command in requested if command)


def stop_with_answer(config: Config, sid: str, answer: str) -> str:
    log_event(config, sid, "final", answer)
    return answer


def agentic_loop(config: Config, prompt: str, history: History | None = None) -> str:
    sid = str(int(time.time() * 1000))
    log_event(config, sid, "start", {"prompt": prompt})

    action = prompt_action(prompt)
    if action:
        return run_direct_action(config, sid, action["cmd"])

    files = collect_files(config)
    instructions = build_system_prompt(config, files)
    payload: object = payload_text(files, prompt, history)
    previous = None
    last_tool = ""
    tool_results: list[str] = []
    commands: list[str] = []

    for _ in range(config.steps_limit):
        used = config.current_tokens = tokens(payload)
        if used > config.session_token_limit:
            log_event(config, sid, "stop", "session token limit reached")
            return "session token limit reached"

        response = call_model(config, instructions, payload, sid, used, previous)
        calls = function_calls(response)
        if not calls:
            return final_answer(config, sid, response, last_tool)

        requested = [call_command(call) for call in calls]
        repeat = repeated_command(requested, commands)
        if repeat:
            answer = tool_summary(last_tool)
            log_event(config, sid, "stop", f"repeated command: {repeat}")
            log_event(config, sid, "final", answer)
            return highlight_commands(answer, sys.stdout)

        remember_commands(commands, requested)
        payload, last_tool = run_tool_calls(config, sid, calls)
        tool_results.append(last_tool)

        if denied(last_tool):
            return stop_with_answer(config, sid, "stopped: command denied")
        if response.get("store") is False:
            if quiet_success(last_tool) and not multi_step_prompt(prompt):
                return stop_with_answer(config, sid, last_tool.strip() or "done")
            payload = followup_payload(files, prompt, tool_results, history)
            previous = None
        else:
            value = response.get("id")
            previous = value if isinstance(value, str) else None

    log_event(config, sid, "stop", "step limit reached")
    return "step limit reached"
