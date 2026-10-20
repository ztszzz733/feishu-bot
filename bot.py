"""飞书机器人：长连接收消息 → 大模型 → 回复。

运行：python bot.py
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import threading
import time
from collections import OrderedDict, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Deque, Dict, List, Optional

import httpx
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from config import Config
from llm import LLMClient, LLMError

log = logging.getLogger("feishu-bot")

# 单条消息长度上限，超过就拆成多条发
REPLY_CHUNK = 3500

FEISHU_DOMAIN = "https://open.feishu.cn"


# --------------------------------------------------------------------------
# 去重：飞书会重投事件，同一条消息可能到达多次
# --------------------------------------------------------------------------
class DedupCache:
    def __init__(self, ttl: int = 600, capacity: int = 2000) -> None:
        self._ttl = ttl
        self._capacity = capacity
        self._lock = threading.Lock()
        self._seen: "OrderedDict[str, float]" = OrderedDict()

    def first_time(self, key: str) -> bool:
        """True 表示第一次见到，False 表示重复。"""
        now = time.time()
        with self._lock:
            while self._seen:
                oldest_at = next(iter(self._seen.values()))
                if now - oldest_at <= self._ttl and len(self._seen) < self._capacity:
                    break
                self._seen.popitem(last=False)

            if key in self._seen:
                return False
            self._seen[key] = now
            return True


# --------------------------------------------------------------------------
# 会话历史：按 chat_id 分开存，纯内存，重启即清空
# --------------------------------------------------------------------------
class ChatHistory:
    def __init__(self, max_turns: int) -> None:
        self._maxlen = max(1, max_turns) * 2
        self._lock = threading.Lock()
        self._store: Dict[str, Deque[dict]] = defaultdict(lambda: deque(maxlen=self._maxlen))

    def get(self, chat_id: str) -> List[dict]:
        with self._lock:
            return list(self._store[chat_id])

    def append(self, chat_id: str, role: str, content: str) -> None:
        with self._lock:
            self._store[chat_id].append({"role": role, "content": content})

    def reset(self, chat_id: str) -> None:
        with self._lock:
            self._store.pop(chat_id, None)


# --------------------------------------------------------------------------
# 机器人
# --------------------------------------------------------------------------
class FeishuBot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.dedup = DedupCache()
        self.history = ChatHistory(config.MAX_HISTORY_TURNS)
        self.bot_open_id: Optional[str] = None

        # 同一个会话的消息串行处理：否则两条消息并发时，
        # 后一条读不到前一条刚写入的历史，模型会答非所问
        self._chat_locks: Dict[str, threading.Lock] = {}
        self._chat_locks_guard = threading.Lock()

        self.llm = LLMClient(
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            model=config.LLM_MODEL,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
            timeout=config.LLM_TIMEOUT,
        )
        self.http = (
            lark.Client.builder()
            .app_id(config.APP_ID)
            .app_secret(config.APP_SECRET)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        self.executor = ThreadPoolExecutor(
            max_workers=config.MAX_WORKERS, thread_name_prefix="llm"
        )

    # ---- 身份 -------------------------------------------------------
    def fetch_bot_open_id(self) -> Optional[str]:
        """查机器人自己的 open_id，用来判断群里是否 @ 了它。失败可降级。"""
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    f"{FEISHU_DOMAIN}/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": self.config.APP_ID, "app_secret": self.config.APP_SECRET},
                )
                token = (resp.json() or {}).get("tenant_access_token")
                if not token:
                    log.error("换取 tenant_access_token 失败：%s", resp.text[:200])
                    return None

                resp = client.get(
                    f"{FEISHU_DOMAIN}/open-apis/bot/v3/info",
                    headers={"Authorization": f"Bearer {token}"},
                )
                bot = (resp.json() or {}).get("bot") or {}
                open_id = bot.get("open_id")
                if open_id:
                    log.info("机器人身份：%s (%s)", bot.get("app_name"), open_id)
                return open_id
        except httpx.HTTPError as exc:
            log.warning("获取机器人身份失败，群里改用宽松的 @ 判断：%s", exc)
            return None

    # ---- 发送 -------------------------------------------------------
    def reply(self, message_id: str, text: str) -> None:
        """回复到原消息，超长自动拆条。"""
        for index, chunk in enumerate(chunk_text(text)):
            body = (
                ReplyMessageRequestBody.builder()
                .content(json.dumps({"text": chunk}, ensure_ascii=False))
                .msg_type("text")
                .uuid(f"{message_id}-{index}")
                .build()
            )
            request = (
                ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
            )

            response = self.http.im.v1.message.reply(request)
            if not response.success():
                log.error(
                    "回复失败 code=%s msg=%s log_id=%s",
                    response.code,
                    response.msg,
                    response.get_log_id(),
                )
                return

    # ---- 事件 -------------------------------------------------------
    def on_message_receive(self, data: P2ImMessageReceiveV1) -> None:
        """事件入口，必须立刻返回，重活丢线程池。"""
        try:
            event = data.event
            if event is None or event.message is None:
                return

            message = event.message
            sender = event.sender
            sender_type = getattr(sender, "sender_type", None)

            # 不响应机器人自己，防止死循环
            if sender_type == "app":
                return

            if not self.dedup.first_time(message.message_id or ""):
                log.debug("重复事件，已忽略：%s", message.message_id)
                return

            chat_type = message.chat_type or "p2p"

            if chat_type != "p2p" and self.config.GROUP_REQUIRE_MENTION:
                if not self._mentioned(message.mentions):
                    return

            self.executor.submit(
                self._handle,
                message.message_id or "",
                message.chat_id or "",
                message.message_type or "",
                message.content or "",
                message.mentions,
            )
        except Exception:
            log.exception("事件分发失败")

    def _mentioned(self, mentions) -> bool:
        if not mentions:
            return False
        if self.bot_open_id:
            return any(getattr(m, "id", None) == self.bot_open_id for m in mentions)
        return True  # 拿不到自己 open_id 时退回宽松判断

    def _lock_for(self, chat_id: str) -> threading.Lock:
        with self._chat_locks_guard:
            lock = self._chat_locks.get(chat_id)
            if lock is None:
                lock = self._chat_locks[chat_id] = threading.Lock()
            return lock

    def _handle(
        self,
        message_id: str,
        chat_id: str,
        message_type: str,
        content: str,
        mentions,
    ) -> None:
        try:
            if message_type != "text":
                self.reply(message_id, f"目前只接文字消息，你发的是 {message_type}。")
                return

            text = extract_text(content, mentions)
            if not text:
                self.reply(message_id, "没读到内容，重发一次？")
                return

            # 同一会话串行：命令和生成都在锁内，避免 /reset 与在途请求打架
            with self._lock_for(chat_id):
                self._respond(message_id, chat_id, text)

        except LLMError as exc:
            log.warning("模型调用失败：%s", exc)
            self.reply(message_id, f"出错了：{exc}")
        except Exception:
            log.exception("处理消息时未预期的错误")
            self.reply(message_id, "内部出错了，看下日志。")

    def _respond(self, message_id: str, chat_id: str, text: str) -> None:
        command = text.lower()
        if command in ("/reset", "/clear", "重置", "清空对话"):
            self.history.reset(chat_id)
            self.reply(message_id, "对话已清空。")
            return
        if command in ("/help", "帮助"):
            self.reply(
                message_id,
                "命令：\n"
                "/reset  清空当前对话历史\n"
                "/ping   测活\n"
                f"模型：{self.config.LLM_MODEL}",
            )
            return
        if command in ("/ping", "ping"):
            self.reply(message_id, "pong")
            return

        messages: List[dict] = [{"role": "system", "content": self.config.SYSTEM_PROMPT}]
        messages.extend(self.history.get(chat_id))
        messages.append({"role": "user", "content": text})

        log.info("[%s] <- %s", chat_id[-8:], _brief(text))
        started = time.time()
        answer = self.llm.chat(messages)
        log.info("[%s] -> %s (%.1fs)", chat_id[-8:], _brief(answer), time.time() - started)

        self.history.append(chat_id, "user", text)
        self.history.append(chat_id, "assistant", answer)

        self.reply(message_id, answer)

    # ---- 启动 -------------------------------------------------------
    def run(self) -> int:
        self.bot_open_id = self.fetch_bot_open_id()

        log.info("模型：%s @ %s", self.config.LLM_MODEL, self.config.LLM_BASE_URL)
        log.info("正在连接飞书长连接…（Ctrl+C 退出）")

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self.on_message_receive)
            .build()
        )

        ws_client = lark.ws.Client(
            self.config.APP_ID,
            self.config.APP_SECRET,
            event_handler=event_handler,
            log_level=lark.LogLevel.WARNING,
            auto_reconnect=True,
        )

        try:
            ws_client.start()
        except KeyboardInterrupt:
            log.info("已退出。")
        except Exception:
            log.exception("长连接异常退出")
            return 1
        finally:
            self.executor.shutdown(wait=False)
            self.llm.close()
        return 0


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def extract_text(content: str, mentions) -> str:
    """从消息 JSON 里取正文，并去掉 @ 占位符。"""
    try:
        payload = json.loads(content) if content else {}
    except (ValueError, TypeError):
        return ""
    text = payload.get("text", "") if isinstance(payload, dict) else ""
    for mention in mentions or []:
        key = getattr(mention, "key", None)
        if key:
            text = text.replace(key, "")
    return text.strip()


def chunk_text(text: str, size: int = REPLY_CHUNK) -> List[str]:
    if len(text) <= size:
        return [text]
    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= size:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, size)
        if cut <= 0:
            cut = size
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


def _brief(text: str, limit: int = 60) -> str:
    return text[:limit].replace("\n", " ") + ("…" if len(text) > limit else "")


def setup_logging(config: Config) -> None:
    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s | %(message)s", "%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            config.LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        log.warning("无法写日志文件 %s: %s", config.LOG_FILE, exc)

    logging.getLogger("lark_oapi").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> int:
    from config import ConfigError

    try:
        config = Config()
    except ConfigError as exc:
        print(f"\n[配置错误] {exc}\n", file=sys.stderr)
        return 2

    setup_logging(config)
    return FeishuBot(config).run()


if __name__ == "__main__":
    sys.exit(main())