# AGENTS.md — 给 AI 协作者的项目约定

飞书 AI 对话机器人：长连接收消息 → OpenAI 兼容模型 → 回复。

## 运行与测试

| 目的 | Windows | Linux |
|---|---|---|
| 单元自测 | `.venv\Scripts\python.exe selftest.py` | `.venv/bin/python selftest.py` |
| 端到端自检（不连飞书） | `.venv\Scripts\python.exe offline_check.py` | `.venv/bin/python offline_check.py` |
| 本地运行 | `.venv\Scripts\python.exe bot.py` | `.venv/bin/python bot.py` |

## 完成定义（DoD）

一个改动算完成，必须同时满足：

1. `selftest.py` 与 `offline_check.py` 都输出 `ALL PASS`
2. 行为变化至少配套一条断言（先写断言，再写实现）
3. Windows 与 Linux 都能跑（不要引入平台相关的路径或命令）

## 硬性约束

- **`.env` 永不入库**：里面是 App Secret 和 LLM Key。改配置只改 `.env.example`。
- **同一应用只能跑一份实例**：本地和服务器同时跑，飞书会把事件随机分给其中一条，表现为"时好时坏"。调试前先 `systemctl stop feishu-bot`。
- **配置语义**：`config.py` 用 `os.environ.setdefault`，**系统环境变量优先于 `.env`**。改了 `.env` 不生效时先查这个。
- **历史只在内存**：按 `chat_id` 隔离，重启即清空。这是已知限制，不要"顺手"改成持久化，除非任务明确要求。

## 代码约定

- 职责边界：`bot.py` 事件编排、`llm.py` 模型调用、`config.py` 配置解析。加新东西前先想清楚放哪。
- 外部调用必须带超时和错误翻译，参考 `llm.py` 的 `_explain_status`。
- 异常边界在 `_handle`：`LLMError` 回人话，其他记完整堆栈。不要在深层吞异常。
- 日志保持 `<- 收到` / `-> 回复` 格式，方便 `journalctl | grep`。
- 同会话串行靠 `_lock_for(chat_id)`；新增会改历史或状态的逻辑要放进同一把锁内。

## 非平凡改动

先在 `tasks/todo.md` 写清：目标、涉及文件、步骤、风险，再动代码。完成后归档到 `tasks/archive/`。

判断"非平凡"的标准：改动超过 3 个文件、涉及并发/锁、涉及发送与回复路径。

## 部署

- 服务器：`ssh ali`（Ubuntu 22.04），代码在 `/root/bot`
- 服务：`feishu-bot.service`，`Restart=always`
- 日志：`journalctl -u feishu-bot -f`
- 更新：`cd /root/bot && git pull && systemctl restart feishu-bot`