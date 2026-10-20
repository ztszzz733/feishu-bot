"""离线自检：不连飞书、不碰真实模型，验证「收到消息 → LLM → 回复」这条链路。

用一个本地假模型服务顶替真实 API，检查：
  - LLMClient 是否按 OpenAI 兼容格式发请求、解析响应
  - 常见错误码是否翻成人话
  - FeishuBot 是否把用户消息喂给模型、把结果回给飞书、并写入历史
  - /ping、/reset 是否短路（不该调用模型）
  - 同一会话并发时是否串行（历史不成对就说明锁失效）

运行：.venv\\Scripts\\python.exe offline_check.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))


class FakeLLMHandler(BaseHTTPRequestHandler):
    """最小 OpenAI 兼容服务，记录请求体供断言。"""

    protocol_version = "HTTP/1.1"
    received: List[dict] = []
    fail_status = None
    fail_body = '{"error": {"message": "invalid api key"}}'
    delay = 0.0

    def log_message(self, *args) -> None:  # 静音
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        type(self).received.append(payload)

        if type(self).delay:
            time.sleep(type(self).delay)

        if type(self).fail_status:
            body = type(self).fail_body.encode("utf-8")
            self.send_response(type(self).fail_status)
        else:
            last_user = ""
            for item in reversed(payload.get("messages") or []):
                if item.get("role") == "user":
                    last_user = item.get("content") or ""
                    break
            body = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": f"收到：{last_user}"}}]},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)

        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLMHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"假模型服务：http://127.0.0.1:{port}")

    os.environ.update(
        {
            "FEISHU_APP_ID": "cli_offline_test",
            "FEISHU_APP_SECRET": "offline_secret",
            "LLM_BASE_URL": f"http://127.0.0.1:{port}",
            "LLM_API_KEY": "sk-offline",
            "LLM_MODEL": "fake-model",
            "SYSTEM_PROMPT": "你是测试助手。",
        }
    )

    from bot import FeishuBot
    from config import Config

    # 指向不存在的文件，避免读到用户真实的 .env
    config = Config(env_path=BASE_DIR / ".env.not-exist")
    bot = FeishuBot(config)

    replies: List[tuple] = []
    bot.reply = lambda message_id, text: replies.append((message_id, text))  # 拦截真实发送

    def send(text: str, chat: str = "oc_offline_chat", mid: str = "om_offline_msg") -> str:
        replies.clear()
        bot._handle(mid, chat, "text", json.dumps({"text": text}, ensure_ascii=False), None)
        return replies[0][1] if replies else ""

    try:
        # 1. 普通消息走通
        out = send("你好")
        assert out == "收到：你好", out
        print("消息 → 模型 → 回复      OK")

        # 2. 请求体格式
        payload = FakeLLMHandler.received[-1]
        assert payload["model"] == "fake-model"
        assert payload["messages"][0] == {"role": "system", "content": "你是测试助手。"}
        assert payload["messages"][-1] == {"role": "user", "content": "你好"}
        assert payload["stream"] is False
        print("请求体格式              OK")

        # 3. 历史被带进下一轮
        send("第二句")
        roles = [m["role"] for m in FakeLLMHandler.received[-1]["messages"]]
        assert roles == ["system", "user", "assistant", "user"], roles
        print("上下文传递              OK")

        # 4. /reset 清空且不调模型
        before = len(FakeLLMHandler.received)
        out = send("/reset")
        assert out == "对话已清空。", out
        assert len(FakeLLMHandler.received) == before
        assert bot.history.get("oc_offline_chat") == []
        print("/reset 短路             OK")

        # 5. /ping 不调模型
        before = len(FakeLLMHandler.received)
        assert send("/ping") == "pong"
        assert len(FakeLLMHandler.received) == before
        print("/ping 短路              OK")

        # 6. 错误码翻人话
        FakeLLMHandler.fail_status = 401
        out = send("随便问问")
        assert "API Key 无效" in out, out
        FakeLLMHandler.fail_status = None
        print("错误码翻译              OK")

        # 7. 非文本消息
        replies.clear()
        bot._handle("om_x", "oc_offline_chat", "image", "{}", None)
        assert "只接文字" in replies[0][1]
        print("非文本兜底              OK")

        # 8. 同一会话并发要串行：不加锁时三个线程会读到同一份旧历史，
        #    每个请求都只带 system+user；加锁后后两个请求能看到前序轮次
        bot.history.reset("oc_conc")
        FakeLLMHandler.delay = 0.3
        before = len(FakeLLMHandler.received)
        threads = [
            threading.Thread(target=send, args=(f"并发{i}",), kwargs={"chat": "oc_conc"})
            for i in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        FakeLLMHandler.delay = 0.0

        widths = sorted(len(b["messages"]) for b in FakeLLMHandler.received[before:])
        assert widths == [2, 4, 6], f"请求携带的上下文不对，疑似未串行：{widths}"

        history = bot.history.get("oc_conc")
        assert [m["role"] for m in history] == ["user", "assistant"] * 3, history
        print(f"会话串行（上下文宽度 {widths}）  OK")

    finally:
        bot.llm.close()
        bot.executor.shutdown(wait=False)
        server.shutdown()

    print()
    print("ALL PASS（未连接飞书，链路逻辑已验证）")
    return 0


if __name__ == "__main__":
    sys.exit(main())