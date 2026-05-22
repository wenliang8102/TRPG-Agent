# Agent Playtest 探索式自测

`scripts/agent_playtest.py` 是给 Codex 或开发者使用的命令行探针，用来像真实玩家一样逐轮和主 Agent 对话。它不预设固定台词；每轮发送什么由操作者根据上一轮回复和状态临场决定。

## 默认行为

- 使用项目真实数据库配置；当前项目默认是 PostgreSQL。
- 默认把观察记录写到：`logs/agent_playtests/`
- 每轮都会保存：
  - `{session_id}.md`：可读 transcript
  - `{session_id}.jsonl`：结构化观察记录
  - `current_session.json`：最近使用的 playtest 会话

这样你可以在 PostgreSQL、前端历史或 trace 里复查 playtest 对话。建议给探索测试使用清晰的 `--session`，例如 `goblin-ambush-playtest`。

## 常用命令

全局参数要放在子命令前：

```powershell
.\.venv\Scripts\python.exe scripts\agent_playtest.py --session my-run send "我沿着三猪小径继续前进，留意两侧灌木。"
```

查看有效配置：

```powershell
.\.venv\Scripts\python.exe scripts\agent_playtest.py doctor
```

发送玩家消息：

```powershell
.\.venv\Scripts\python.exe scripts\agent_playtest.py send "我检查路上的死马和灌木。"
```

读取当前状态：

```powershell
.\.venv\Scripts\python.exe scripts\agent_playtest.py state
```

模拟前端结束回合按钮：

```powershell
.\.venv\Scripts\python.exe scripts\agent_playtest.py end-turn player_hero
```

进入交互式 REPL：

```powershell
.\.venv\Scripts\python.exe scripts\agent_playtest.py interactive
```

REPL 内置命令：

- `/state`：读取当前图状态
- `/end-turn <actor_id>`：模拟前端结束回合按钮
- `/session`：打印当前 session id
- `/quit`：退出

## 建议观察点

- 开战是否经过 `prepare_combat_start` 与 `combat_start` workflow。
- 战斗开始后是否有地图、单位落点、先攻顺序与当前行动者。
- 怪物或 AI 托管友方回合，主 Agent 是否先调用 `delegate_combat_turn`，而不是直接落子。
- 执行器回传中是否有 `tool_trace`、`combat_events`、`recommended_next`。
- 玩家或玩家接管友方回合是否能通过 `end-turn` 模拟前端按钮推进。
- 战斗收束是否经过 `prepare_combat_end`，俘虏、投降、非致命击败是否计入 XP。

## 临时 SQLite 测试

项目默认使用 PostgreSQL。如果只想做一次完全隔离的临时测试，可在命令前显式覆盖：

```powershell
$env:TRPG_DATABASE_BACKEND = "sqlite"
$env:TRPG_MEMORY_DB_PATH = "backend/data/agent_playtest_memory.sqlite3"
.\.venv\Scripts\python.exe scripts\agent_playtest.py doctor
```

测完建议清掉这两个环境变量，避免后续探针误用临时存储。
