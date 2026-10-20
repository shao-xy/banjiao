---
name: banjiao
description: 面向单教师的教学事务持久化 Skill：管理课程（含单双周/节次/学校时间表）、课表、进度、作业、教学事务；管理课程文件（每课独立目录、固定 7 角色：幻灯片/讲义/资料/作业/提交/进度参考/大纲）；支持大纲读取与"下一讲建议"的事实卡。所有写入通过 `scripts/skill.py` 子进程落到本地 SQLite（无 HTTP 后端常驻），可选 FastAPI 网页查看。**不**做教案生成/试卷/评语/通知文案——那些交给大模型原生提示词。
---

# 伴教 Skill（面向 AI Agent 的教学事务 Skill）

> 单机、单教师、面向 AI Agent 的教学事务持久化 Skill。
> 把"课表 / 作业 / 进度 / 事务"留在这里、把"写通知 / 出教案 / 出题目"留给大模型原生能力。

---

## 0. 形态约束（必须遵守）

1. **后端不常驻**。网页 FastAPI 服务只在教师要"看一眼"时才临时启动，用 Ctrl-C 关闭。
2. **数据写入通过 LLM 入口脚本** `scripts/skill.py` 直接落到 SQLite，**不依赖 HTTP 后端**。
3. **Skill 只负责持久化与查询**：课程、进度、作业、教学事务、文件、大纲（可选：向量检索）。
4. **不属于 Skill**：教案生成、试卷、评语、通知文案……**全部交给大模型原生提示词能力**，不要在 Skill 中实现。

---

## 1. Skill 能力边界

| 类别 | 是否属于 Skill |
| --- | --- |
| 持久化课程信息 / 课表（今日、本周） | ✅ |
| 记录每节课程进度、与大纲对比 | ✅ |
| 作业 CRUD + 到期提醒 | ✅ |
| 教学事务 CRUD + 到点提醒 | ✅ |
| 结构化查询（按周 / 按截止 / 按状态） | ✅ |
| 教学文档向量索引与语义检索 | ✅（可选开关） |
| 教案生成 / 试卷 / 评语 / 通知文案 | ❌（大模型原生） |
| 多教师 / 多用户 / 权限 | ❌（明确不做） |
| 后端常驻进程 / systemd 部署 | ❌（单机按需） |

---

## 2. 与上层 Agent（Host Runtime）交互

Skill 对外**只输出结构化 JSON**，由上层 Agent 把 JSON 翻成自然语言回复教师。

### 2.1 调用 Skill 修改数据

大模型在对话中识别"教师想改数据"，通过子进程调用入口脚本：

```bash
python scripts/skill.py <action> --json '{...}'                # 短参
python scripts/skill.py <action> --json-file path.json          # Windows 推荐（避免转义）
echo '{...}' | python scripts/skill.py <action> --json @-       # stdin
```

stdout 是**一行 JSON**：

```json
{"ok": true, "data": {...}}                                                          # 成功
{"ok": false, "error": {"code":"...","message":"...","status":N,"hint":"...","extra":{...}}}  # 失败
```

退出码恒为 0，**判断成败只看 `ok` 字段**。错误码语义：

| code | 含义 | 处理建议 |
| --- | --- | --- |
| `bad_param` | 字段非法 | 看 `error.message` 修正参数 |
| `missing_param` | 必填字段缺失 | 向用户询问后再次调用 |
| `not_found` | id 不存在 | 调 list 类接口核对 |
| `bad_json` | --json 无法解析 | 重传，注意 UTF-8 / 转义 |
| `vector_disabled` | 向量检索未启用 | 见 §6 开启 |
| `vector_unavailable` | 未装 sentence-transformers | 见 §6 |
| `unknown_action` | action 拼错 | 查本文档 §4 |
| `internal_error` | 未捕获异常 | 报告给用户 + 查日志 |

### 2.2 查询

```bash
python scripts/skill.py course.today
python scripts/skill.py course.week --json '{"start":"2026-10-20"}'
python scripts/skill.py homework.due_soon
python scripts/skill.py vec.search --json '{"query":"极限定义","top_k":3}'
```

### 2.3 提醒

启动 `scripts/reminder_daemon.py`（可选），周期性扫描到期作业 / 事务，stdout 推送标准化 JSON：

```json
{"type":"homework.due","emitted_at":"2026-10-25T08:00:00","data":{...}}
```

上层 Agent 解析后**主动推送**给教师。

### 2.4 文本创作类请求

教师要求"帮我写一封周末答疑通知" → **不要调 Skill**，直接让大模型回答。

---

## 3. 数据存储

- SQLite 单文件 `data/banjiao.db`（WAL 模式 + synchronous=FULL）
- 启动时自动建表，无需迁移；字段若有新增，重启即可幂等补齐
- 网页启动不写数据；写只通过 `scripts/skill.py` 或 API

---

## 4. Actions（完整列表）

| action | 参数要点 | 用途 |
| --- | --- | --- |
| `course.list` / `course.get` / `course.create` / `course.update` / `course.delete` | create 需要 `name`；其余最少传 `id` | 课程 CRUD（含 `week_pattern`、`period_no` 等周次/节次字段） |
| `course.today` / `course.week` | 无参 / `start` + `weeks` | 当日 / 未来 N 周课表（按 `week_pattern` / `weeks_range` / `sections` 过滤） |
| `course.apply_period` | `id`, `force?` | 用学校时间表把节次填成起止时间（连排取最早-最晚；`force=true` 强制重算） |
| `course.set_outline_path` | `id`, `filename` | 指定课程的大纲文件（必须落在"大纲"子目录里） |
| `course.get_outline` | `id` | 大纲文件状态：`has_file` / `cache_present` / `stale` / `text` / `cached` |
| `course.refresh_outline` | `id`, `parsed={weeks:[{week,topics:[...]},...]}` | 大模型解析完大纲后回填缓存（严格结构校验） |
| `course.suggest_outline` | `id`, `topics:["第1章 …"]`, `weeks_total?` | 按教学周均匀分配章节，给出周次映射**建议**（不落库） |
| `course.next_topic` | `id`, `target_date?` | 事实卡：target_week / current_week / current_week_source / lag_weeks / outline_target / hint |
| `progress.list` / `progress.create` / `progress.update` / `progress.delete` | create 需要 `course_id, lesson_date, topic`；可选 `outline_week` | 课程进度 |
| `homework.list` / `homework.create` / `homework.update` / `homework.delete` / `homework.due_soon` | create 需要 `title, published_at` | 作业 |
| `affair.list` / `affair.create` / `affair.update` / `affair.delete` / `affair.mark_reminded` | create 需要 `title` | 教学事务 |
| `fs.init` / `fs.list_top_dirs` | 无参 | 初始化 / 列出 4 个顶层文件夹（含 binding 状态） |
| `fs.bind_course` / `fs.unbind_course` | `top_kind`, `dirname`, `course_id` / `id` | 把顶层子目录绑定到课程 |
| `fs.scan_course` / `fs.scan_top_dir` | `course_id` / `top_kind`, `dirname` | 增量扫描（mtime + sha256） |
| `course.list_files` | `course_id`, `role?` | 列出当前文件（DB 缓存视图） |
| `fs.archive` | `path` | 把文件/文件夹打快照到 `data/_archive/<ts>_*`（不删原内容） |
| `fs.import_file` | `src`, `top_kind?`, `dirname?`, `filename?`, `role?`, `archive?` | 从外部路径导入文件到 `文件/<子目录>/`，并建索引（绕开 shell 路径转义） |
| `fs.list_files` | `top_kind`, `dirname`, `subdir?` | 列出顶层归档目录下的文件（不需要 course_id） |
| `fs.read_file` | `course_id`, `role`, `filename` | 读一个文件；文本返回 content，二进制返回 content_base64；>20MB 拒绝 |
| `fs.file_history` | `course_id`, `role`, `filename` | 列出版本历史（current + superseded） |
| `fs.write_text` | `course_id`, `role`, `filename`, `text` | 硬覆盖写文本；二进制后缀拒绝；自动同步 DB |
| `fs.delete` | `path`, `confirm?` | 删除；含内容必须 `confirm="YES_DELETE_<sha8>"` |
| `school.get_schedule` / `school.set_schedule` | `schedule={morning,afternoon,evening:[{no,start,end}]}` | 学校课程时间表（落盘 `data/_school_schedule.json`） |
| `doc.list` / `doc.add_text` / `doc.add_file` / `doc.remove` | 索引文档 | 教学文档入库 |
| `vec.search` / `vec.stats` | `query`, `top_k` | 向量检索 |

### 4.1 课程周次与节次字段

**`weekday` 对照表（极易踩坑，务必核对）**

| 值 | 星期 | 常见课程 |
|---|---|---|
| `0` | 周一 | |
| `1` | **周二** | 周二上午高等数学 |
| `2` | **周三** | 周三下午操作系统 |
| `3` | 周四 | |
| `4` | 周五 | |
| `5` | 周六 | |
| `6` | 周日 | |

> 建课前先和教师确认"周几"，把中文转成上表数字。填错会导致整周课表错位。

**周次模式 `week_pattern`** ∈ `all` / `odd` / `even` / `custom`

- `all`：整个学期每周都上课（默认）
- `odd`：仅奇数周（1,3,5…）
- `even`：仅偶数周（2,4,6…）
- `custom`：仅 `weeks` 列出的周次

**周次范围 `weeks_range`**（B3，避免手工展开）

- 传 `weeks_range=[1,7]` + `week_pattern="odd"` → 第 1,3,5,7 周（等价于 `weeks=[1,3,5,7]`）
- 传 `weeks_range=[1,15]` + `week_pattern="all"` → 第 1..15 周
- `weeks_range` 与 `weeks` **不可同时传**

**节次**

- `period_no`：单节（1..12）
- `period_nos`：连排多节，如 `[3,4,5]` 表示 3-5 节连排（必须升序）
- 调 `course.apply_period` 自动从学校时间表填 `start_time`/`end_time`
  （连排会取最早开始 + 最晚结束）
- 学校课表调整后，用 `course.apply_period --json '{"id":"...","force":true}'` 强制重算

**多时段 `sections`（B1：一门课一周多个时段）**

```json
"sections": [
  {"weekday": 1, "week_pattern": "all", "period_nos": [3, 4, 5]},
  {"weekday": 2, "week_pattern": "odd", "weeks_range": [1, 7], "period_nos": [8, 9]},
  {"weekday": 1, "week_pattern": "odd", "weeks_range": [9, 15], "period_nos": [1, 2]}
]
```

上例表示：周二 3-5 节全期 + 周三 8-9 节单双周 1-7 周 + 周二 1-2 节单双周 9-15 周。

- `sections` 为空/不传时，退回用扁平字段（`weekday`/`period_no`/…）作为唯一时段
- 课表查询（`course.today` / `course.week`）会把多时段**按段展开成多行**，每行带自己的 `period_nos` 和 `start_time`/`end_time`
- 大纲与进度仍挂在课程层（`course.set_outline_path` / `progress.create`），不按段拆分

### 4.2 文件系统角色（固定 7 个）

`slides` / `lecture` / `material` / `homework` / `submission` / `reference` / `outline`

分别对应中文文件夹名 `幻灯片` / `讲义` / `资料` / `作业` / `提交` / `进度参考` / `大纲`。教师在角色上的其它需求不由 Skill 管理。

**课程目录 vs 顶层归档目录**

- **课程目录**（已 `fs.bind_course` 绑定）：下面有 7 个角色子目录，用 `fs.scan_course` / `course.list_files`
- **顶层归档目录**（如 `文件/资料/`、`文件/规章制度/`）：**没有**角色结构，用：
  - `fs.import_file --json '{"src":"<绝对路径>","top_kind":"文件","dirname":"资料","role":"material"}'` 把外部文件导入并建索引
  - `fs.list_files --json '{"top_kind":"文件","dirname":"资料"}'` 列出该目录下所有文件（含子目录）

### 4.3 删除/备份硬约束

- `fs.delete` 任何**有内容**的文件/目录，必须传 `confirm="YES_DELETE_<sha8>"`（sha8 在首次报错时返回）
- `fs.archive` 永远不删原内容；只是复制到 `_archive/`
- `fs.write_text` 二进制后缀（`.pdf/.pptx/.docx/...`）一律拒绝；改二进制请用操作系统

### 4.4 传参方式（跨平台 + 避开编码坑）

| 方式 | 用法 | 适用 |
|---|---|---|
| **推荐**：`--json-file` | `python scripts/skill.py <action> --json-file p.json` | 所有平台；自动兼容 UTF-8 BOM |
| `--out-file` | `python scripts/skill.py <action> --out-file r.json` | 结果写文件（UTF-8 无 BOM），绕开 shell `>` 重定向 |
| stdin | `Get-Content p.json \| python scripts/skill.py <action> --json @-` | 管道场景 |
| `--json` 直接传 | `python scripts/skill.py <action> --json '{"name":"x"}'` | bash/zsh 最稳；**某些 shell 会吃掉引号**，见下 |

> ⚠️ **Windows PowerShell 5.1** 会把 `'{"name":"x"}'` 里的双引号吃掉，Skill 会尽力自动修补；
> 但如果值里含 `,` `:` 等字符可能修补失败。**遇到 `bad_json` 请改用 `--json-file`。**
>
> ⚠️ **不要用 `>` / `Out-File` 重定向结果**（PowerShell 会写 UTF-16LE + BOM），用 `--out-file`。

---

## 5. 异常处理契约

- 任何不存在的 `id` → 返回 `not_found` 错误，**绝不返回 200 + 空对象**。
- 时间格式错（如 `start_time="25:00"`）→ Pydantic `bad_param`。
- 参数缺失 → `missing_param` + `hint` 告诉上层 Agent 去问用户。
- 内部异常 → `internal_error` 500，附 stack 不外泄。
- 脚本退出码恒为 0；判断成败只看 stdout 中的 `ok` 字段。

---

## 6. 向量模块（可选）

默认关闭。启用步骤：

```bash
# 1) 安装依赖
python -m pip install sentence-transformers

# 2) 启用（设置系统环境变量；Skill 不读取 .env 文件）
#    PowerShell:
$env:BANJIAO_ENABLE_VECTOR = "1"
#    bash / zsh:
export BANJIAO_ENABLE_VECTOR=1

# 3) 把大纲 / 课件放到 docs/ 目录（与 .py 同级）

# 4) 调用 Skill 入库
python scripts/skill.py doc.add_file --json '{"title":"高等数学大纲","rel_path":"calculus.md","course_id":"<课id>"}'

# 5) 检索
python scripts/skill.py vec.search --json '{"query":"极限的定义与性质","top_k":3}'
```

关闭：把 `BANJIAO_ENABLE_VECTOR` 设为 `0` 或取消设置，所有 `vec.*` action 返回 `vector_disabled`。

> ⚠️ **不要创建 `.env` 文件**——本 Skill 只读系统环境变量，`.env` 会被静默忽略。

---

## 7. 本地运行提要

```bash
# 一次性安装（推荐虚拟环境）
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 改数据 → 直接和 Skill 对话（或手动 skill.py）
# 看网页 → 执行 run_web.py，用完 Ctrl-C 关闭
python run_web.py
# → http://127.0.0.1:30826
```

详细使用见 [README.md](./README.md)。

---

## 7. 一键安装

把以下文本贴给新对话的 AI Agent：

```text
请安装并演示伴教 Skill：
https://github.com/shao-xy/banjiao/releases/download/v0.2.1/banjiao-slim-v0.2.1.zip

下载到你系统的临时目录并解压；装 requirements.txt；跑 scripts/demo.py；
把每步输出贴给我。失败的话告诉我错在哪，不要放弃。
```

---

## 8. 开源协议

本项目采用 **Apache License 2.0**。详见 [LICENSE](./LICENSE)。

核心条款：
- ✅ 允许商业使用、修改、再分发
- ❗ 必须保留版权声明与 LICENSE
- ❗ 修改文件需显著标记
- ❌ 不得用作者名字背书衍生作品
