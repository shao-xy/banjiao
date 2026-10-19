# 伴教（Banjiao）· 面向 AI Agent 的教学事务 Skill

> 一个单机、单教师的教学事务 Skill，AI Agent 通过命令行直接读写本地 SQLite 数据，网页前端只是"看一眼"的临时工具。

---

## 1. 这是什么

- **Skill 接口**：[scripts/skill.py](file:///d:/0000-SXY/1-Projects/伴教/banjiao/scripts/skill.py) —— AI Agent 调用此脚本读写数据，不依赖 HTTP。
- **可视化前端**：[run_web.py](file:///d:/0000-SXY/1-Projects/伴教/banjiao/run_web.py) —— 临时启动 FastAPI + 极简网页，看完关闭。
- **持久化**：SQLite 单文件 `data/banjiao.db`，无外部数据库服务。
- **可选**：向量索引模块，启用后可对教学文档做语义检索。

**不属于 Skill 的事项**（交还给大模型原生能力）：教案、试卷、评语、通知文案、PPT……

---

## 2. 为什么这样设计

| 选择 | 理由 |
| --- | --- |
| 单机 SQLite | 单教师，无需账号 / 同步；备份 = 复制一个文件 |
| 后端按需启动 | 用户日常只用大模型；想要"看一眼"才拉起服务 |
| LLM 入口脚本而非纯 HTTP API | 持久化操作走子进程调用，免开服务、延迟低、容易调试 |
| 极简网页 | 只读可视化；复杂操作走对话 |
| 向量检索可选 | 默认关闭减少依赖；想要时打开 |

---

## 3. 目录结构

```
banjiao/                         # Skill 根目录
├── SKILL.md                     # 给上层 Agent 看的接口契约
├── README.md                    # 本文件
├── src/                         # Python 包（import banjiao）
│   └── banjiao/
│       ├── __init__.py
│       ├── config.py            # 配置（环境变量 + .env）
│       ├── llm_errors.py        # 标准化错误
│       ├── models.py            # Pydantic 数据模型
│       ├── db.py                # SQLite 持久化
│       ├── service.py           # 业务逻辑（被 skill.py 和 api.py 共用）
│       ├── vector_index.py      # 可选向量检索
│       ├── api.py               # FastAPI（按需启动）
│       └── web/                 # 极简只读网页
│           ├── index.html
│           ├── app.js
│           └── style.css
├── scripts/
│   ├── skill.py                 # LLM 入口脚本（核心）
│   └── reminder_daemon.py       # 可选：提醒守护
├── tests/                       # 边界 / 回归测试
├── data/                        # 运行时：SQLite 数据库（gitignore）
├── vector_store/                # 运行时：向量索引（gitignore）
├── docs/                        # 默认向量检索语料目录
├── run_web.py                   # 临时启动网页
├── requirements.txt
└── .gitignore
```

Skill 根目录与 Python 包同名（`banjiao`），它们位于不同的路径层级 —— 这是有意为之的清晰命名。

---

## 4. 安装

```powershell
# 进入 Skill 根目录
cd banjiao

# 推荐 venv（避免污染系统 Python）
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

依赖：

- 必需：`fastapi`、`uvicorn`、`pydantic`、`pydantic-settings`
- 可选：`sentence-transformers`（仅启用向量检索时需要）

---

## 5. 使用

### 5.1 让 AI Agent 直接改数据

模型对话中识别到教师想改课表 / 作业 / 事务 / 进度，**Agent 调用**：

```powershell
# 创建课程
python scripts\skill.py course.create --json-file course.json

# 看今日课表
python scripts\skill.py course.today

# 标记作业已提交
python scripts\skill.py homework.update --json '{"id":"<hid>","status":"submitted"}'

# 添加教研事务
python scripts\skill.py affair.create --json '{"title":"教研会","kind":"meeting","due_at":"2026-10-20T15:00:00","remind_before_minutes":60}'

# 把大纲文件加入向量索引
python scripts\skill.py doc.add_file --json '{"title":"高等数学大纲","rel_path":"calculus.md"}'

# 检索大纲
python scripts\skill.py vec.search --json '{"query":"极限定义","top_k":3}'
```

完整 action 表与字段定义见 [SKILL.md](./SKILL.md) §4。

输出是**一行 JSON**：

```json
{"ok": true,  "data": {...}}
{"ok": false, "error": {"code":"...", "message":"...", "hint":"...", "extra":{...}}}
```

### 5.2 临时启动网页（用完关闭）

```powershell
python run_web.py
# → 控制台打印 "伴教 临时网页服务已启动 → http://127.0.0.1:30826"
# → 浏览器打开该地址；Ctrl-C 结束服务
```

也可以指定端口：

```powershell
python run_web.py --port 8090
```

网页**只读** —— 课程名、班级、作业列表、事务列表、进度记录，按 Tab 切换视图；没有"创建 / 编辑"按钮，那些操作交给大模型对话。

### 5.3 启用提醒

可选启动守护进程（轮询 due-soon 项）：

```powershell
# 默认每 60 秒扫一次
python scripts\reminder_daemon.py

# 自定义轮询间隔
python scripts\reminder_daemon.py --interval 30
```

stdout 会持续输出 JSON 事件，如：

```json
{"type":"homework.due","emitted_at":"2026-10-19T08:00:00","data":{"id":"...","title":"习题 3.1","due_at":"2026-10-25",...}}
{"type":"affair.due","emitted_at":"2026-10-20T14:00:00","data":{"id":"...","title":"教研会","due_at":"2026-10-20T15:00:00",...}}
```

> 进程内已自动把"已推送"的事务标记 `reminded=true`，避免重复推送；想重新推送可调用 `affair.mark_reminded --json '{"id":"..."}'` 把字段改回 `false`。

### 5.4 启用向量检索（可选）

```powershell
# 1) 装依赖
.venv\Scripts\python -m pip install sentence-transformers

# 2) 在 Skill 根目录创建 .env
"BANJIAO_ENABLE_VECTOR=1" | Out-File -Encoding ascii .env

# 3) 把大纲 / 课件放进 docs/

# 4) 入库
python scripts\skill.py doc.add_file --json '{"title":"课程大纲","rel_path":"calculus.md"}'

# 5) 检索
python scripts\skill.py vec.search --json '{"query":"极限定义","top_k":3}'
```

关闭：删除 `.env` 或 `BANJIAO_ENABLE_VECTOR=0`，重启后 `vec.*` action 返回 `vector_disabled`。

---

## 6. 环境变量（.env 也支持）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `BANJIAO_HOST` | `127.0.0.1` | 网页监听地址 |
| `BANJIAO_PORT` | `30826` | 网页监听端口 |
| `BANJIAO_ENABLE_VECTOR` | `0` | 是否启用向量检索 |
| `BANJIAO_VECTOR_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 嵌入模型 |

---

## 7. 测试

`tests/test_edge.py` 提供 98 项生产安全边界 / 回归断言，覆盖：

- 数据完整性（Pydantic 字段 / model_validator：长度 / 类型 / 范围 / 枚举 / 组合约束）
- 课程周次（weeks=[]、学期间、空 weekday、start_time 倒置）
- 作业与事务（含取消、submitted 重置、due vs published）
- 提醒与时间判定（due_soon / overdue / reminded 幂等）
- 持久化与并发（SQLite WAL + 事务隔离）
- SQL 注入与危险字符（参数化 / Unicode / 长度截断）
- 错误处理（标准化 JSON / 不泄露路径）
- JSON 序列化（date / time / UUID / None round-trip）
- LLM 入口脚本子进程端到端（exit 恒为 0、stdin / 文件两种）

```powershell
python -u tests/test_edge.py
```

输出彩色高亮 ✅ / ❌，打印 `通过 N / N` 或失败明细。

---

## 8. 故障排查

| 症状 | 排错 |
| --- | --- |
| `ModuleNotFoundError: pydantic_settings` | 没装依赖：`pip install -r requirements.txt` |
| `unable to open database file` | 检查 CWD；SQLite 写入 `data/banjiao.db` 相对路径要求 CWD 是 Skill 根 |
| `vector_disabled` | 没启用；见 §5.4 |
| `vector_unavailable` | 没装 `sentence-transformers`；同上 |
| 重复收到同一条提醒 | 进程重启会清空内存去重；如果想完全重发，update 把 `reminded` 改回 `false` |

---

## 9. 重置 / 备份

```powershell
# 备份：复制一个文件即可
copy data\banjiao.db backup\banjiao-2026-10-19.db

# 重置：删 data 目录
del data\banjiao.db
```

向量索引在 `vector_store/`，文档原文件在 `docs/`，重置时按需清理。
