import sys, os, json, subprocess, threading, urllib.request, urllib.error, select, termios, tty, time, shutil
from dataclasses import dataclass, field
from itertools import count
from math import sin
from pathlib import Path

@dataclass
class Config:
    api_key: str = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    url: str = os.getenv("URL", "https://openrouter.ai/api/v1/responses")
    model: str = os.getenv("MODEL", "openai/gpt-5.5")
    steps_limit: int = int(os.getenv("AGENT_STEPS_LIMIT", "100"))
    max_output_tokens: int = int(os.getenv("MAX_OUTPUT_TOKENS", "1024"))
    max_tool_chars: int = int(os.getenv("MAX_TOOL_CHARS", "8192"))
    session_token_limit: int = int(os.getenv("SESSION_TOKEN_LIMIT", "65536"))
    approve_mode: str = os.getenv("AGENT_APPROVE", "ask").lower()
    sessions: str = os.getenv("AGENT_SESSIONS", "agent_sessions.json")
    working_dir: str = field(default_factory=os.getcwd)
    current_tokens: int = 0

def spinner_line(text, i, size=15, levels="⣀⣤⣶⣷⣿", empty=" ", fixed=False):
    palette = "38;5;52 38;5;88 38;5;124 38;5;160 38;5;196 38;5;203 38;5;196 38;5;160".split()
    color = lambda c, s: f"\33[{c}m{s}\33[0m"
    limit = size - len(text); p = i % (limit * 2) if limit > 0 else 0
    start = max(0, limit // 2) if fixed else 0 if limit <= 0 else min(p, limit * 2 - p)

    def cell(x):
        if start <= x < start + len(text): return color("1;160", text[x - start])
        v = ((sin(i * .45 + x * .9) + sin(i * .25 - x * 1.4)) / 2 + 1) / 2
        ch = (empty + levels)[round(v * len(levels))]
        return color(90 if ch == empty else palette[(x + i // 2) % len(palette)], ch)
    return "  [" + "".join(cell(x) for x in range(size)) + "]"

def tokens(x) -> int: return len(x if isinstance(x, str) else json.dumps(x)) // 4
def ansi(text, code, stream=sys.stdout): return f"\33[{code}m{text}\33[0m" if stream.isatty() else text
def trace(text): print("\r\33[K" + ansi(f"# {text}", "94", sys.stderr), file=sys.stderr, flush=True)
def command_line(command, stream=sys.stdout): return ansi(f"$ {command}", "92", stream)

def highlight_commands(text: str, stream=sys.stdout) -> str:
    return "\n".join(command_line(line[2:], stream) if line.startswith("$ ") else line for line in text.splitlines())

def show_tool_result(result: str):
    if result.strip(): print(highlight_commands(result.strip(), sys.stderr), file=sys.stderr)

def spinner(done, text="thinking", fixed=False, status=""):
    if not sys.stderr.isatty(): return
    for i in count():
        if done.wait(0.075): break
        print("\r" + spinner_line(text, i, fixed=fixed) + (" " + status if status else "") + "\33[K", end="", file=sys.stderr, flush=True)
    print("\r\33[K", end="", file=sys.stderr, flush=True)

def prompt_line(config: Config, buf: str, i: int, width=None) -> str:
    status = f" [{config.current_tokens + tokens(buf)} / {config.session_token_limit}] "
    width = width or shutil.get_terminal_size((80, 20)).columns
    available = max(0, width - 19 - len(status) - 1)
    shown = buf if len(buf) <= available else ("<" + buf[-available + 1:] if available > 1 else "")
    return "\r" + spinner_line("prompt", i, fixed=True) + status + shown + "\33[K"

def prompt_input(config: Config) -> str:
    if not sys.stdin.isatty(): return input()
    fd = sys.stdin.fileno(); old = termios.tcgetattr(fd); buf = ""; done = False
    try:
        tty.setcbreak(fd)
        for i in count():
            print(prompt_line(config, buf, i), end="", file=sys.stderr, flush=True)
            if not select.select([sys.stdin], [], [], 0.075)[0]: continue
            ch = sys.stdin.read(1)
            if ch in ("\n", "\r"): done = True; print(file=sys.stderr); return buf
            if ch == "\x03": raise KeyboardInterrupt
            elif ch == "\x04" and not buf: raise EOFError
            elif ch in ("\x7f", "\b"): buf = buf[:-1]
            elif ch >= " ": buf += ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if not done: print("\r\33[K", end="", file=sys.stderr, flush=True)

def collect_files(config: Config, trace_reads=True) -> dict[str, str]:
    root = Path(config.working_dir); paths = [root / "README.md", root / "AGENTS.md"]
    skills = root / ".agents/skills"
    if skills.exists(): paths += sorted(skills.rglob("SKILL.md"))
    files = {str(p.resolve()): p.read_text() for p in paths if p.exists()}
    if trace_reads:
        for p in files: trace(f"read {p}")
    return files

def build_system_prompt(config: Config, files=None) -> str:
    files = files or collect_files(config); find = lambda s: ", ".join(p for p in files if p.endswith(s)) or f"no {s} defined"
    env = f"cwd={config.working_dir} os={sys.platform} py={sys.version.split()[0]} shell={os.getenv('SHELL','?')}"
    ctx = f"readme={find('README.md')} agents={find('AGENTS.md')} skills={find('SKILL.md')}"
    role = "You are a tiny shell coding agent. Use execute_shell to inspect/edit/test. Stay inside cwd. Be concise."
    return f"{role}\nenvironment: {env}\ncontext: {ctx}"

def tool_schema() -> dict:
    props = {"command": {"type": "string"}, "description": {"type": "string"}, "cwd": {"type": ["string", "null"]}, "timeout": {"type": "integer"}, "env": {"type": "object", "additionalProperties": {"type": "string"}}}
    params = {"type": "object", "properties": props, "required": ["command", "description"], "additionalProperties": False}
    return {"type": "function", "name": "execute_shell", "description": "Run a shell command.", "parameters": params}

TOOL = tool_schema()
def log_event(config: Config, sid: str, event: str, data):
    path = Path(config.working_dir) / Path(config.sessions).name
    log = json.loads(path.read_text()) if path.exists() else []
    log.append({"time": time.time(), "session": sid, "event": event, "data": data}); path.write_text(json.dumps(log, indent=2))

def approve(config: Config, args: dict) -> bool:
    print(f"\n# {args.get('description', 'run command')}", file=sys.stderr)
    print(command_line(args.get("command", ""), sys.stderr), file=sys.stderr)
    if config.approve_mode == "all": return True
    try: choice = input("Approve? [y] yes  [a] all  [n] no: ").strip().lower()
    except EOFError: return False
    if choice in ("a", "all"): config.approve_mode = "all"; return True
    return choice in ("y", "yes")

def safe_cwd(config: Config, cwd) -> tuple[str, str]:
    root = os.path.abspath(config.working_dir); path = os.path.abspath(cwd or root)
    if not os.path.isdir(path): return root, f"ignored invalid cwd: {path}\n"
    if os.path.commonpath([root, path]) != root: return root, f"ignored outside cwd: {path}\n"
    return path, ""

def execute_shell(config: Config, command: str, description="", cwd=None, timeout=120, env=None) -> str:
    if not approve(config, {"command": command, "description": description, "cwd": cwd, "timeout": timeout, "env": env}):
        return "DENIED: user denied command"
    try:
        done = threading.Event(); thread = threading.Thread(target=spinner, args=(done, "running"), kwargs={"fixed": True})
        thread.start()
        try:
            trace(f"run {command}")
            run_env = {**os.environ, **(env or {})}
            run_cwd, cwd_note = safe_cwd(config, cwd)
            p = subprocess.run(command, shell=True, cwd=run_cwd, env=run_env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            return f"$ {command}\n{cwd_note}exit {p.returncode}\n{p.stdout}"[-config.max_tool_chars:]
        finally:
            done.set(); thread.join()
    except (EOFError, OSError, subprocess.TimeoutExpired) as e:
        return f"{type(e).__name__}: {e}"[-config.max_tool_chars:]

def prompt_action(prompt: str) -> dict | None:
    p = prompt.strip()
    if p.startswith("run "): return {"cmd": p[4:]}
    if p.startswith(("cat ", "ls ", "pwd", "find ", "rg ", "sed ")): return {"cmd": p}
    if "agent_sessions.json" in p and any(w in p.lower() for w in ("read", "content", "show")): return {"cmd": "cat agent_sessions.json"}
    return None

def call_model(config: Config, instructions: str, payload, sid: str, used: int, previous=None) -> dict:
    if not config.api_key: raise SystemExit("set OPENROUTER_API_KEY")
    body = {"model": config.model, "instructions": instructions, "tools": [TOOL], "input": payload, "max_output_tokens": config.max_output_tokens}
    if previous: body["previous_response_id"] = previous
    log_event(config, sid, "api_request", body)
    req = urllib.request.Request(config.url, json.dumps(body).encode(), {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"})
    trace(f"api {config.model} [{used} / {config.session_token_limit}]")
    done = threading.Event(); thread = threading.Thread(target=spinner, args=(done, "thinking"), kwargs={"fixed": True, "status": f"[{used} / {config.session_token_limit}]"}) ; thread.start()
    try:
        with urllib.request.urlopen(req) as response: data = json.load(response)
        log_event(config, sid, "api_response", data); return data
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace"); message = f"HTTP {e.code}: {detail or e.reason}"
        log_event(config, sid, "api_error", message); return {"output_text": f"api error: {message}"}
    except Exception as e:
        log_event(config, sid, "api_error", str(e)); raise
    finally:
        if thread: done.set(); thread.join()

def response_text(response: dict) -> str:
    if response.get("output_text"): return response["output_text"]
    return "".join(part.get("text", "") for item in response.get("output", []) if item.get("type") == "message" for part in item.get("content", []) if isinstance(part, dict) and part.get("type") == "output_text")

def useful_text(text: str) -> bool:
    text = text.strip(); return bool(text) and any(ch.isalnum() for ch in text)

def tool_summary(result: str) -> str:
    statuses = [line for line in result.strip().splitlines() if line.startswith("exit ")]
    if statuses and all(status == "exit 0" for status in statuses) and not tool_stdout(result).strip(): return "done"
    return result.strip() or "done"

def tool_stdout(result: str) -> str:
    out = []; after_status = False
    for line in result.strip().splitlines():
        if line.startswith("$ "): after_status = False
        elif line.startswith("exit "): after_status = True
        elif after_status: out.append(line)
    return "\n".join(out)

def quiet_success(result: str) -> bool:
    statuses = [line for line in result.strip().splitlines() if line.startswith("exit ")]
    return bool(statuses) and all(status == "exit 0" for status in statuses) and not tool_stdout(result).strip()

def denied(result: str) -> bool: return any(line.startswith("DENIED:") for line in result.splitlines())
def multi_step_prompt(prompt: str) -> bool:
    p = " " + prompt.lower() + " "
    return any(mark in p for mark in (" then ", " and ", ",", ";", " after ", " also ", "\n"))

def tool_output(config: Config, call: dict) -> dict:
    try: args = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError as e: result = f"bad arguments: {e}"
    else: result = execute_shell(config, **args) if call.get("name") == "execute_shell" else "unknown tool"
    show_tool_result(result)
    return {"type": "function_call_output", "call_id": call["call_id"], "output": result}

def call_command(call: dict) -> str:
    try: return json.loads(call.get("arguments") or "{}").get("command", "")
    except json.JSONDecodeError: return ""

def history_text(history) -> str: return "\n\n".join(f"User: {prompt}\nAssistant: {answer}" for prompt, answer in history)
def payload_text(files: dict[str, str], prompt: str, history=None) -> str:
    past = f"\n\nPrevious conversation:\n{history_text(history)}" if history else ""
    return "\n\n".join(f"# {p}\n{t}" for p, t in files.items()) + past + f"\n\nUser: {prompt}"

def followup_payload(files: dict[str, str], prompt: str, tool_results: list[str], history=None) -> str:
    results = "\n\n".join(f"Tool result {i}:\n{result}" for i, result in enumerate(tool_results, 1))
    return payload_text(files, prompt, history) + f"\n\nTool results so far:\n{results}\n\nContinue from these results. Do not repeat a command that already succeeded unless the user explicitly asked to verify. If the task is complete, answer briefly. If more work is needed, call execute_shell again."

def agentic_loop(config: Config, prompt: str, history=None) -> str:
    sid = str(int(time.time() * 1000)); log_event(config, sid, "start", {"prompt": prompt})
    if action := prompt_action(prompt):
        result = execute_shell(config, action["cmd"], "user requested direct shell command"); log_event(config, sid, "tool_result", result)
        return highlight_commands(result.strip(), sys.stdout)
    files = collect_files(config); instructions = build_system_prompt(config, files)
    payload, previous, last_tool, tool_results, commands = payload_text(files, prompt, history), None, "", [], []
    for _ in range(config.steps_limit):
        used = config.current_tokens = tokens(payload)
        if used > config.session_token_limit:
            log_event(config, sid, "stop", "session token limit reached"); return "session token limit reached"
        response = call_model(config, instructions, payload, sid, used, previous)
        calls = [x for x in response.get("output", []) if x.get("type") == "function_call"]
        if not calls:
            answer = response_text(response)
            if not useful_text(answer): answer = tool_summary(last_tool)
            log_event(config, sid, "final", answer); return highlight_commands(answer, sys.stdout)
        requested = [call_command(call) for call in calls]
        repeats = [cmd for cmd in requested if cmd and cmd in commands]
        if repeats:
            answer = tool_summary(last_tool)
            log_event(config, sid, "stop", f"repeated command: {repeats[0]}"); log_event(config, sid, "final", answer)
            return highlight_commands(answer, sys.stdout)
        commands += [cmd for cmd in requested if cmd]
        payload = [tool_output(config, call) for call in calls]; last_tool = "\n".join(x["output"] for x in payload); tool_results.append(last_tool); log_event(config, sid, "tool_result", payload)
        if denied(last_tool):
            answer = "stopped: command denied"
            log_event(config, sid, "final", answer); return answer
        if response.get("store") is False:
            if quiet_success(last_tool) and not multi_step_prompt(prompt):
                answer = last_tool.strip() or "done"
                log_event(config, sid, "final", answer); return answer
            payload, previous = followup_payload(files, prompt, tool_results, history), None
        else:
            previous = response.get("id")
    log_event(config, sid, "stop", "step limit reached"); return "step limit reached"

def repl(config: Config):
    print("choom repl. /exit to quit.")
    history = []
    while True:
        print()
        config.current_tokens = tokens(payload_text(collect_files(config, trace_reads=False), "", history))
        try: prompt = prompt_input(config).strip()
        except (EOFError, KeyboardInterrupt): print(); return
        if prompt in ("/exit", "/quit"): return
        if prompt:
            answer = agentic_loop(config, prompt, history)
            history.append((prompt, answer))
            print(answer)

if __name__ == "__main__":
    config = Config()
    if sys.argv[1:]: print(agentic_loop(config, " ".join(sys.argv[1:])))
    else: repl(config)
