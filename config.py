"""配置加载：读取同目录下的 .env，缺项给出明确报错。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


class ConfigError(RuntimeError):
    pass


def load_env(path: Path) -> None:
    """极简 .env 解析，避免额外依赖。已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_PLACEHOLDERS = {"", "your_key_here", "cli_xxxxxxxxxxxx", "sk-xxxxxxxx"}


def _get(key: str, default: str = "", required: bool = False) -> str:
    value = os.environ.get(key, "").strip()
    if value in _PLACEHOLDERS:
        value = default
    if required and not value:
        raise ConfigError(f"缺少配置项 {key}，请复制 .env.example 为 .env 并填写。")
    return value


def _get_bool(key: str, default: bool = False) -> bool:
    return _get(key, "true" if default else "false").lower() in ("1", "true", "yes", "on")


def _get_int(key: str, default: int) -> int:
    try:
        return int(_get(key, str(default)))
    except ValueError:
        return default


def _get_float(key: str, default: float) -> float:
    try:
        return float(_get(key, str(default)))
    except ValueError:
        return default


class Config:
    """一次性把环境变量读成配置对象。缺必需项时抛 ConfigError。"""

    def __init__(self, env_path: Path = ENV_PATH) -> None:
        load_env(env_path)

        # ---- 飞书应用 ----
        self.APP_ID = _get("FEISHU_APP_ID", required=True)
        self.APP_SECRET = _get("FEISHU_APP_SECRET", required=True)

        # ---- 大模型（OpenAI 兼容接口）----
        self.LLM_BASE_URL = _get("LLM_BASE_URL", "https://api.deepseek.com")
        # 本地模型（Ollama / vLLM）通常不校验 Key，别逼用户随便编一个
        self.LLM_IS_LOCAL = any(
            host in self.LLM_BASE_URL for host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")
        )
        self.LLM_API_KEY = _get("LLM_API_KEY", required=not self.LLM_IS_LOCAL)
        self.LLM_MODEL = _get("LLM_MODEL", "deepseek-chat")
        self.LLM_TEMPERATURE = _get_float("LLM_TEMPERATURE", 1.0)
        self.LLM_MAX_TOKENS = _get_int("LLM_MAX_TOKENS", 2048)
        self.LLM_TIMEOUT = _get_int("LLM_TIMEOUT", 120)

        # ---- 机器人行为 ----
        self.SYSTEM_PROMPT = _get(
            "SYSTEM_PROMPT",
            "你是我的私人助理。回答简洁、直接、有主见，不说客套话。"
            "中文回复，除非我明显在用别的语言。",
        )
        self.MAX_HISTORY_TURNS = _get_int("MAX_HISTORY_TURNS", 10)
        self.GROUP_REQUIRE_MENTION = _get_bool("GROUP_REQUIRE_MENTION", True)
        self.MAX_WORKERS = _get_int("MAX_WORKERS", 4)
        self.LOG_LEVEL = _get("LOG_LEVEL", "INFO").upper()
        self.LOG_FILE = _get("LOG_FILE", str(BASE_DIR / "bot.log"))