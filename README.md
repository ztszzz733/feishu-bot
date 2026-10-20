# 飞书 AI 机器人

飞书自建应用 + 长连接，把大模型接到飞书里聊天。不需要公网 IP、不需要服务器、不需要 HTTPS 证书。

## 一、在飞书开放平台创建应用

1. 打开 https://open.feishu.cn/app → **创建企业自建应用**
2. 填名称、图标，创建
3. 进入应用 → **添加应用能力** → 勾选 **机器人**
4. 左侧 **权限管理**，搜索并开通（**四个都要**）：
   - `im:message.p2p_msg:readonly`（读取用户发给机器人的单聊消息）
   - `im:message.group_at_msg:readonly`（接收群聊中 @ 机器人的消息）
   - `im:message:send_as_bot`（以应用的身份发消息）
   - `im:message`（获取与发送单聊、群组消息，兜底）

   > 只开 `im:message` 时，"添加事件"那步常会提示权限不足——事件本身要求的是上面那两个 `readonly` 权限。
5. 左侧 **事件与回调** → **事件配置**
   - 订阅方式选 **使用长连接接收事件**
   - 添加事件：**接收消息 v2.0**（`im.message.receive_v1`）
6. **凭证与基础信息** 里复制 `App ID` 和 `App Secret`
7. **版本管理与发布** → 创建版本 → 申请发布（自建应用一般即时通过）

> 关键：订阅方式必须是**长连接**，不是 HTTP 回调。否则程序在自己电脑上收不到事件。

## 二、配置

```powershell
copy .env.example .env
notepad .env
```

必填三项：

| 变量 | 从哪拿 |
|---|---|
| `FEISHU_APP_ID` | 凭证与基础信息 |
| `FEISHU_APP_SECRET` | 凭证与基础信息 |
| `LLM_API_KEY` | 模型服务商的 API Key |

## 三、跑起来

先做一次离线自检（不需飞书凭据，用本地假模型验证链路）：

```powershell
.venv\Scripts\python.exe offline_check.py
```

看到 `ALL PASS` 说明「消息 → 模型 → 回复」逻辑没问题，再去填 `.env` 里那三个真值。

然后正式启动：

```powershell
.venv\Scripts\python.exe bot.py
```

或者双击 `run.bat`。

看到这行就说明连上了：

```
机器人身份：xxx (ou_xxxxx)
正在连接飞书长连接…
```

然后在飞书里搜应用名，点开单聊，直接说话。

## 四、可用命令

| 命令 | 作用 |
|---|---|
| `/reset` | 清空当前对话历史 |
| `/ping` | 测活 |
| `/help` | 帮助 |

## 五、换模型

改 `.env` 里的 `LLM_BASE_URL` 和 `LLM_MODEL`，只要对方是 OpenAI 兼容协议就能用：

| 服务 | LLM_BASE_URL | LLM_MODEL |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `qwen2.5:7b` |

## 六、已经处理好的坑

- **事件重投**：飞书会重复推同一条消息，按 `message_id` 去重（10 分钟窗口）
- **自己回自己**：过滤 `sender_type == "app"`，防止死循环
- **群里刷屏**：默认只在 @ 机器人时回复，用 `bot/v3/info` 拿到自己的 open_id 精确判断
- **@ 占位符**：正文里的 `@_user_1` 会被剥掉再喂给模型
- **长回复**：超过 3500 字自动拆成多条，按换行处切
- **发消息幂等**：每条回复带 `uuid`，重发不会重复
- **模型超时/限流**：自动重试一次，错误码翻成人话回给你
- **多线程**：事件回调立刻返回，LLM 调用在线程池里跑，不阻塞其他消息

## 七、常见问题

**连上了但发消息没反应**

- 开放平台 → 事件与回调，确认订阅方式是「长连接」且已添加 `im.message.receive_v1`
- 应用是否已发布？未发布的版本不会生效
- 看 `bot.log` 有没有收到事件

**报 230013 / 无权限**

- 权限没开通，或应用未发布
- 单聊场景需要你先在飞书里主动给机器人发一条消息（建立会话）

**换 token 失败**

- `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 填错了

**接本地 Ollama 起不来**

- 已修：`LLM_BASE_URL` 指向 `127.0.0.1` / `localhost` 时 `LLM_API_KEY` 可以不填

## 八、部署到服务器（Ubuntu 22.04，常驻）

长连接模式下服务器**不需要公网 IP、域名或证书**，只要能主动访问 `open.feishu.cn:443`。
（有公网 IP 也不影响，只是用不上。）

### 1. 拉代码 + 装依赖

```bash
sudo apt update && sudo apt install -y python3 python3-venv git

git clone https://github.com/ztszzz733/feishu-bot.git ~/feishu-bot
cd ~/feishu-bot
```

> 国内服务器装包慢的话：`pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/`

### 2. 传 `.env`（不入库，必须单独送）

在**本地**执行：

```powershell
scp D:\code\dsh_ws\butler\feishu-bot\.env user@你的服务器IP:~/feishu-bot/.env
```

服务器上收紧权限：

```bash
chmod 600 ~/feishu-bot/.env
```

### 3. 一键部署

```bash
chmod +x deploy/install.sh
./deploy/install.sh
```

脚本会：建 venv → 装 `requirements.lock` 里的精确版本 → 跑离线自检 → 写 systemd 服务 → 开机自启并启动。

也可以手动做（等价）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python offline_check.py     # 应当 ALL PASS
sudo cp deploy/feishu-bot.service /etc/systemd/system/   # 先改里面的 YOUR_USER 和路径
sudo systemctl daemon-reload && sudo systemctl enable --now feishu-bot
```

### 4. 常用运维命令

```bash
journalctl -u feishu-bot -f          # 实时日志
sudo systemctl restart feishu-bot    # 重启
sudo systemctl status feishu-bot     # 状态
sudo systemctl disable --now feishu-bot   # 停止并取消自启
```

### 5. 更新代码

```bash
cd ~/feishu-bot
git pull
.venv/bin/pip install -r requirements.lock   # 依赖有变时
sudo systemctl restart feishu-bot
```

### ⚠️ 只能跑一份

同一个应用开两条长连接（比如本地还开着 `bot.py`），飞书会把事件**随机分给其中一条**，
表现为"时好时坏、日志对不上"。部署完成后，本地必须停掉。

## 九、下一步可以加什么

- 卡片消息 + 按钮（`im.v1.message.patch` 可以实现"流式"逐字更新）
- 文件收发（`im.v1.file.create` 先上传拿 `file_key`）
- 日程/任务（`calendar/v4`、`task/v2`）——把"明天三点开会"直接写进日历
- 消息持久化（SQLite 替掉内存里的 `ChatHistory`）
- 主动推送（定时任务调用 `im.v1.message.create` 发到你自己的 chat_id）