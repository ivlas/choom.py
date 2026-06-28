import json
import os
from dataclasses import dataclass, field
from typing import Any


def env(name: str, default: str) -> Any:
    return field(default_factory=lambda: os.getenv(name, default))


def env_int(name: str, default: int) -> Any:
    return field(default_factory=lambda: int(os.getenv(name, str(default))))


def env_bool(name: str) -> Any:
    return field(default_factory=lambda: os.getenv(name, "").lower() in ("1", "true", "yes", "on"))


def api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    local = os.getenv("URL", "").startswith(("http://localhost", "http://127.0.0.1", "http://[::1]"))
    return key or ("local" if local else "")


@dataclass
class Config:
    api_key: str = field(default_factory=api_key)
    url: str = env("URL", "https://openrouter.ai/api/v1/responses")
    model: str = env("MODEL", "openai/gpt-5.5")
    steps_limit: int = env_int("AGENT_STEPS_LIMIT", 100)
    max_output_tokens: int = env_int("MAX_OUTPUT_TOKENS", 1024)
    max_tool_chars: int = env_int("MAX_TOOL_CHARS", 8192)
    session_token_limit: int = env_int("SESSION_TOKEN_LIMIT", 65536)
    stream: bool = env_bool("AGENT_STREAM")
    reasoning_summary: str = env("AGENT_REASONING", "")
    sessions: str = env("AGENT_SESSIONS", "agent_sessions.json")
    working_dir: str = field(default_factory=os.getcwd)
    current_tokens: int = 0
    streamed_output: bool = False


def tokens(value: object) -> int:
    text = value if isinstance(value, str) else json.dumps(value)
    return len(text) // 4
