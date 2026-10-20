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

- 必需：`fastapi`、`uvicorn`、`pydantic`（**不**需要 `pydantic-settings`——Skill 自写最小读取）
- 可选：`sentence-transformers`（仅启用向量检索时需要）

---

## 5. 使用

### 5.1 让 AI Agent 直接改数据

模型对话中识别到教师想改课表 / 作业 / 事务 / 进度 / 大纲，**Agent 调用**：

```powershell
# 创建课程（含单双周 + 学期 + 节次）
python scripts\skill.py course.create --json '{"name":"高等数学","class_group":"2024-A","weekday":2,"semester_start":"2026-09-07","semester_end":"2027-01-10","week_pattern":"odd","start_time":"08:00","end_time":"09:50","period_no":1}'

# 把学校第一节的 08:00-08:45 填到 start_time/end_time
python scripts\skill.py course.apply_period --json '{"id":"<cid>"}'

# 看今日课表
python scripts\skill.py course.today

# 标记作业已提交
python scripts\skill.py homework.update --json '{"id":"<hid>","status":"submitted"}'

# 添加教研事务
python scripts\skill.py affair.create --json '{"title":"教研会","kind":"meeting","due_at":"2026-10-20T15:00:00","remind_before_minutes":60}'

# ============== 文件系统（每课独立目录） ==============
# 1) 初始化 4 个顶层文件夹（教学/科研/事务/文件）
python scripts\skill.py fs.init

# 2) 把课程绑定到顶层子目录（必须先在文件系统里建好同名子目录）
python scripts\skill.py fs.bind_course --json '{"top_kind":"教学","dirname":"高等数学","course_id":"<cid>"}'

# 3) 增量扫描（mtime 不变 → 复用 sha256）
python scripts\skill.py fs.scan_course --json '{"course_id":"<cid>"}'

# 4) 读大纲（文本→字符串；其它→base64）
python scripts\skill.py fs.read_file --json '{"course_id":"<cid>","role":"outline","filename":"outline.md"}'

# 5) 大模型解析完大纲后回填 cache（Skill 不调 LLM）
python scripts\skill.py course.refresh_outline --json '{"id":"<cid>","parsed":{"weeks":[{"week":1,"topics":["极限定义","极限运算"]}]}}'

# 6) 算出"今天该讲哪"
python scripts\skill.py course.next_topic --json '{"id":"<cid>","target_date":"2026-10-20"}'

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

# 2) 设置环境变量（PowerShell 当前会话）
$env:BANJIAO_ENABLE_VECTOR = "1"

# 3) 把大纲 / 课件放进 docs/

# 4) 入库
python scripts\skill.py doc.add_file --json '{"title":"课程大纲","rel_path":"calculus.md"}'

# 5) 检索
python scripts\skill.py vec.search --json '{"query":"极限定义","top_k":3}'
```

关闭：`BANJIAO_ENABLE_VECTOR=0`（或取消设置）后 `vec.*` action 返回 `vector_disabled`。

> 历史版本曾用过 `.env` 文件；当前实现**不读取** `.env`，全部走系统环境变量，避免冷启动延迟。

---

## 6. 环境变量（系统变量；不读 .env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `BANJIAO_HOST` | `127.0.0.1` | 网页监听地址 |
| `BANJIAO_PORT` | `30826` | 网页监听端口 |
| `BANJIAO_DATA_DIR` | `SKILL_ROOT/data` | SQLite + 文件系统根目录（测试常用 `temp/banjiao_test_*` 隔离） |
| `BANJIAO_FILES_DIR` | `SKILL_ROOT/files` | 预留：教师主动分发的资料根目录 |
| `BANJIAO_VECTOR_DIR` | `SKILL_ROOT/vector_store` | 向量索引落盘目录 |
| `BANJIAO_DOCS_DIR` | `SKILL_ROOT/docs` | 默认向量检索语料目录 |
| `BANJIAO_ENABLE_VECTOR` | `0` | 是否启用向量检索（`1/true/yes/on` 视为开启） |
| `BANJIAO_VECTOR_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 嵌入模型 |

---

## 7. 测试

| 文件 | 数量 | 覆盖范围 |
| --- | --- | --- |
| [tests/test_edge.py](file:///d:/0000-SXY/1-Projects/伴教/banjiao/tests/test_edge.py) | 98 | 数据完整性（Pydantic 字段 / model_validator）；课程周次边界；作业与事务；提醒与时间判定；持久化与并发；SQL 注入；标准化错误；JSON 序列化；LLM 入口脚本端到端 |
| [tests/test_fs_edge.py](file:///d:/0000-SXY/1-Projects/伴教/banjiao/tests/test_fs_edge.py) | 29 | 阶段① 文件系统骨架（路径约束 / 绑定 / 增量扫描 / 硬删确认） |
| [tests/test_schedule_edge.py](file:///d:/0000-SXY/1-Projects/伴教/banjiao/tests/test_schedule_edge.py) | 40 | 阶段② 单双周/区间周 + 节次 + 学校课程时间表 + fs.archive |
| [tests/test_fileio_edge.py](file:///d:/0000-SXY/1-Projects/伴教/banjiao/tests/test_fileio_edge.py) | 31 | 阶段③ 文件读写（read_file / write_text / 20MB 上限 / 二进制后缀拒绝 / 历史版本） |
| [tests/test_outline_edge.py](file:///d:/0000-SXY/1-Projects/伴教/banjiao/tests/test_outline_edge.py) | 34 | 阶段④ 大纲读取/缓存/进度结合（next_topic 事实卡） |

合计 **232 项断言**，本地全绿。

```powershell
# 跑某一组
python -u tests\test_edge.py
python -u tests\test_fs_edge.py
python -u tests\test_schedule_edge.py
python -u tests\test_fileio_edge.py
python -u tests\test_outline_edge.py
```

输出彩色高亮 ✅ / ❌，打印 `通过 N / N` 或失败明细。所有测试都用 `BANJIAO_DATA_DIR` 隔离到 `temp/banjiao_test_*`，不污染默认 `data/`。

---

## 8. 故障排查

| 症状 | 排错 |
| --- | --- |
| `ModuleNotFoundError: pydantic` | 没装依赖：`pip install -r requirements.txt` |
| `unable to open database file` | 检查 CWD；SQLite 写入 `data/banjiao.db` 相对路径要求 CWD 是 Skill 根 |
| `vector_disabled` | 没启用；见 §5.4 |
| `vector_unavailable` | 没装 `sentence-transformers`；同上 |
| 重复收到同一条提醒 | 进程重启会清空内存去重；如果想完全重发，update 把 `reminded` 改回 `false` |
| 子进程 `course.create` 第一次很慢（>5s）| 本机 Python 冷启动抖动；连续调用后会稳定在毫秒级 |

---

## 9. 重置 / 备份

```powershell
# 备份：复制一个文件即可
copy data\banjiao.db backup\banjiao-2026-10-19.db

# 重置：删 data 目录
del data\banjiao.db
```

向量索引在 `vector_store/`，文档原文件在 `docs/`，重置时按需清理。

---

## 10. 开源协议

本项目采用 **Apache License 2.0**（详见 [LICENSE](./LICENSE)）。这意味着你可以：

- ✅ **商用** —— 集成进商业产品 / 服务
- ✅ **修改** —— 按你的需求调整
- ✅ **再分发** —— 自由分享给他人

但你必须：

- ❗ 保留原始版权声明和 LICENSE 文件
- ❗ 在修改过的文件上做出显著标记
- ❗ 若分发 `NOTICE`（如有），必须随附保留

并且**不能**：

- ❌ 用作者 / 贡献者名字背书你的衍生产品（除非获得书面许可）
- ❌ 因使用本 Skill 造成的损害要求作者承担责任
