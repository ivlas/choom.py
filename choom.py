import sys, os, json, subprocess, threading, urllib.request, select, termios, tty, time
from dataclasses import dataclass, field
from itertools import count
from math import sin
from pathlib import Path

def _ignore() -> frozenset[str]:
    text = Path(".gitignore").read_text() if Path(".gitignore").exists() else ""
    return frozenset({p for x in text.splitlines() if (p := x.strip().strip("/")) and not p.startswith(("#", "!"))} | {".git"})

@dataclass(frozen=True)
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
    ignore: frozenset[str] = field(default_factory=_ignore)

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

def tokens(x) -> int:
    return len(x if isinstance(x, str) else json.dumps(x)) // 4

def spinner(done, text="thinking", fixed=False, status=""):
    if not sys.stderr.isatty(): return
    for i in count():
        if done.wait(0.075): break
        print("\r" + spinner_line(text, i, fixed=fixed) + (" " + status if status else "") + "\33[K", end="", file=sys.stderr, flush=True)
    print("\r\33[K", end="", file=sys.stderr, flush=True)

def prompt_input(config: Config) -> str:
    if not sys.stdin.isatty(): return input()
    fd = sys.stdin.fileno(); old = termios.tcgetattr(fd); buf = ""; done = False
    try:
        tty.setcbreak(fd)
        for i in count():
            print("\r" + spinner_line("prompt", i, fixed=True) + f" [0 / {config.session_token_limit}] " + buf + "\33[K", end="", file=sys.stderr, flush=True)
            if not select.select([sys.stdin], [], [], 0.075)[0]: continue
            ch = sys.stdin.read(1)
            if ch in ("\n", "\r"): done = True; print(file=sys.stderr); return buf
            if ch == "\x03": raise KeyboardInterrupt
            if ch == "\x04" and not buf: raise EOFError
            if ch in ("\x7f", "\b"): buf = buf[:-1]
            elif ch >= " ": buf += ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if not done: print("\r\33[K", end="", file=sys.stderr, flush=True)

def collect_files(config: Config) -> dict[str, str]:
    root = Path(config.working_dir); paths = [root / "README.md", root / "AGENTS.md"]
    skills = root / ".agents/skills"
    if skills.exists(): paths += sorted(skills.rglob("SKILL.md"))
    return {str(p.resolve()): p.read_text() for p in paths if p.exists()}

def build_system_prompt(config: Config) -> str:
    files = collect_files(config); find = lambda s: ", ".join(p for p in files if p.endswith(s)) or f"no {s} defined"
    env = f"cwd={config.working_dir} os={sys.platform} py={sys.version.split()[0]} shell={os.getenv('SHELL','?')}"
    ctx = f"readme={find('README.md')} agents={find('AGENTS.md')} skills={find('SKILL.md')}"
    role = "You are tiny zero-dependency coding agent. Stay inside cwd. Reply JSON only: {\"cmd\":\"...\"} or {\"final\":\"...\"}."
    return f"{role}\nenvironment: {env}\ncontext: {ctx}"

def log_event(config: Config, sid: str, event: str, data):
    path = Path(config.working_dir) / Path(config.sessions).name
    log = json.loads(path.read_text()) if path.exists() else []
    log.append({"time": time.time(), "session": sid, "event": event, "data": data})
    path.write_text(json.dumps(log, indent=2))

def execute_shell(config: Config, cmd: str) -> dict:
    try:
        if config.approve_mode != "all" and input(f"\nrun? {cmd}\n[y/N] ").lower() not in ("y", "yes"):
            return {"cmd": cmd, "code": None, "stdout": "", "stderr": "skipped"}
        done = threading.Event(); thread = threading.Thread(target=spinner, args=(done, "running"), kwargs={"fixed": True})
        thread.start()
        try:
            p = subprocess.run(cmd, shell=True, cwd=config.working_dir, text=True, capture_output=True, timeout=120)
            return {"cmd": cmd, "code": p.returncode, "stdout": p.stdout[-config.max_tool_chars:], "stderr": p.stderr[-config.max_tool_chars:]}
        finally:
            done.set(); thread.join()
    except (EOFError, subprocess.TimeoutExpired) as e:
        return {"cmd": cmd, "code": None, "stdout": "", "stderr": str(e)}

def parse_action(text: str) -> dict:
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        action = json.loads(text[text.find("{"):text.rfind("}") + 1])
        if "cmd" in action or "final" in action: return action
    except Exception:
        pass
    return {"final": text or "empty model response"}

def call_model(config: Config, messages: list[dict], sid: str, used: int) -> str:
    if not config.api_key: raise SystemExit("set OPENROUTER_API_KEY")
    payload = {"model": config.model, "input": messages, "max_output_tokens": config.max_output_tokens}
    log_event(config, sid, "api_request", payload)
    body = json.dumps(payload).encode()
    req = urllib.request.Request(config.url, body, {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"})
    done = threading.Event(); thread = threading.Thread(target=spinner, args=(done, "thinking"), kwargs={"fixed": True, "status": f"[{used} / {config.session_token_limit}]"})
    thread.start()
    try:
        data = json.loads(urllib.request.urlopen(req).read()); log_event(config, sid, "api_response", data)
        if data.get("output_text"): return data["output_text"]
        for item in data.get("output", []):
            for part in item.get("content", []):
                if isinstance(part, dict) and part.get("text"): return part["text"]
                if isinstance(part, str): return part
        return json.dumps(data)
    except Exception as e:
        log_event(config, sid, "api_error", str(e)); raise
    finally:
        done.set(); thread.join()

def agentic_loop(config: Config, prompt: str) -> str:
    sid = str(int(time.time() * 1000)); log_event(config, sid, "start", {"prompt": prompt})
    files = "\n\n".join(f"# {p}\n{t}" for p, t in collect_files(config).items())
    messages = [{"role": "system", "content": build_system_prompt(config)}, {"role": "user", "content": f"{files}\n\nUser: {prompt}"}]
    for _ in range(config.steps_limit):
        used = tokens(messages)
        if used > config.session_token_limit:
            log_event(config, sid, "stop", "session token limit reached"); return "session token limit reached"
        reply = parse_action(call_model(config, messages, sid, used)); log_event(config, sid, "action", reply)
        messages.append({"role": "assistant", "content": json.dumps(reply)})
        if "final" in reply:
            log_event(config, sid, "final", reply["final"]); return reply["final"]
        result = execute_shell(config, reply["cmd"]); log_event(config, sid, "tool_result", result)
        messages.append({"role": "user", "content": json.dumps(result)})
    log_event(config, sid, "stop", "step limit reached"); return "step limit reached"

def repl(config: Config):
    print("choom repl. /exit to quit.")
    while True:
        print()
        try: prompt = prompt_input(config).strip()
        except (EOFError, KeyboardInterrupt): print(); return
        if prompt in ("/exit", "/quit"): return
        if prompt: print(agentic_loop(config, prompt))

if __name__ == "__main__":
    config = Config()
    if sys.argv[1:]: print(agentic_loop(config, " ".join(sys.argv[1:])))
    else: repl(config)
