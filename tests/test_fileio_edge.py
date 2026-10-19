"""阶段 ③ 测试：文件读写 + 历史 + 文本写入。

新增 action：
    fs.read_file      --json '{"course_id":"...","role":"outline","filename":"outline.md"}'
    fs.file_history   --json '{"course_id":"...","role":"outline","filename":"outline.md"}'
    fs.write_text     --json '{"course_id":"...","role":"outline","filename":"outline.md","text":"..."}'

约束：
    文本后缀（.md/.txt/.csv）→ content 字符串；其它后缀 → content_base64
    write_text 只允许文本后缀；二进制后缀一律拒绝
    写入硬覆盖；mtime 变化触发增量扫描；旧 current 进 history
    文件不存在 → not_found
    未绑定课程 → not_found
    filename 含 .. 或绝对路径 → bad_param
    文件大小 > 20MB → file_too_large
"""

from __future__ import annotations

import base64
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


# ===== 复用最小测试框架 =====

class _Color:
    OK = "\033[92m"
    FAIL = "\033[91m"
    END = "\033[0m"


_failures: list[tuple[str, str]] = []
_passes = 0
_section = ""


def section(name: str) -> None:
    global _section
    _section = name
    print(f"\n=== {name} ===")


def expect(label: str, cond: bool, detail: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  {_Color.OK}PASS{_Color.END} {label}")
    else:
        msg = f"[{_section}] {label}" + (f"  -- {detail}" if detail else "")
        _failures.append((_section, msg))
        print(f"  {_Color.FAIL}FAIL{_Color.END} {label}{('  -- ' + detail) if detail else ''}")


def expect_raises(label: str, fn, exc_type=Exception, contains: str = "") -> None:
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        if isinstance(e, exc_type):
            haystack = str(e)
            if contains and contains not in haystack:
                expect(label, False, f"抛错但消息不含 {contains!r}：{haystack[:300]}")
                return
            expect(label, True)
            return
        expect(label, False, f"抛出 {type(e).__name__} 而非 {exc_type.__name__}: {e}")
        return
    expect(label, False, "未抛错")


# ===== 隔离数据目录 =====

_TEST_DIR = Path(tempfile.mkdtemp(prefix="banjiao_fileio_test_"))
os.environ["BANJIAO_DATA_DIR"] = str(_TEST_DIR / "data")
os.environ.setdefault("BANJIAO_ENABLE_VECTOR", "0")

for mod in list(sys.modules):
    if mod.startswith("banjiao."):
        del sys.modules[mod]
sys.modules.pop("banjiao", None)

from banjiao import config, db as _db_mod, service, models  # noqa: E402
from banjiao.config import settings  # noqa: E402
from banjiao.llm_errors import SkillError  # noqa: E402


def cleanup() -> None:
    if _TEST_DIR.exists():
        shutil.rmtree(_TEST_DIR, ignore_errors=True)


def fresh_data_dir() -> None:
    try:
        _db_mod.db._conn.close()
    except Exception:
        pass
    _db_mod.db._conn = None
    if _TEST_DIR.exists():
        shutil.rmtree(_TEST_DIR, ignore_errors=True)
    _TEST_DIR.mkdir(parents=True, exist_ok=True)
    (_TEST_DIR / "data").mkdir(parents=True, exist_ok=True)
    _db_mod.db.__init__()


# ===== 通用工具 =====

def make_bound_course(name: str = "数学分析") -> models.Course:
    service.fs_init()
    c = service.create_course({"name": name})
    top = settings.data_dir / "教学" / name
    top.mkdir(parents=True, exist_ok=True)
    for r in ("幻灯片", "讲义", "资料", "作业", "提交", "进度参考", "大纲"):
        (top / r).mkdir(exist_ok=True)
    service.fs_bind_course("教学", name, c.id)
    return c


# ===== 测试组 =====

def t_文本读取() -> None:
    section("3.1 fs.read_file 文本文件")
    fresh_data_dir()
    c = make_bound_course()
    p = settings.data_dir / "教学" / "数学分析" / "大纲" / "outline.md"
    p.write_text("# 第 1 章\n极限与连续\n", encoding="utf-8")
    out = service.fs_read_file(c.id, "大纲", "outline.md")
    expect("encoding=utf-8", out["encoding"] == "utf-8")
    expect("content 是字符串", isinstance(out["content"], str))
    expect("content 含 # 第 1 章", "# 第 1 章" in out["content"])
    expect("sha256 长度 64", len(out["sha256"]) == 64)
    expect("path 字段正确", out["path"] == "教学/数学分析/大纲/outline.md")


def t_二进制读取() -> None:
    section("3.2 fs.read_file 二进制 → base64")
    fresh_data_dir()
    c = make_bound_course()
    p = settings.data_dir / "教学" / "数学分析" / "幻灯片" / "ch1.pptx"
    raw = b"\x50\x50\x54\xFA" + b"\x00\x01\x02" * 16  # 类 pptx 字节
    p.write_bytes(raw)
    out = service.fs_read_file(c.id, "slides", "ch1.pptx")
    # service 接受英文 role 枚举值
    expect("encoding=base64", out["encoding"] == "base64")
    expect("role 标准化为 slides", out["role"] == "slides")
    decoded = base64.b64decode(out["content_base64"])
    expect("base64 解码与原字节一致", decoded == raw)
    expect("size 字段正确", out["size"] == len(raw))


def t_路径与不存在() -> None:
    section("3.3 路径安全 + 不存在报错")
    fresh_data_dir()
    c = make_bound_course()
    # 不存在的文件
    expect_raises(
        "不存在的文件 → not_found",
        lambda: service.fs_read_file(c.id, "大纲", "nope.md"),
        SkillError,
        contains="不存在",
    )
    # filename 路径穿越（..）
    expect_raises(
        "filename .. 穿越 → bad_param",
        lambda: service.fs_read_file(c.id, "大纲", "../etc/passwd"),
        Exception,
        contains="..",
    )
    # 未绑定课程
    c2 = service.create_course({"name": "外语"})
    expect_raises(
        "未绑定课程 → not_found",
        lambda: service.fs_read_file(c2.id, "大纲", "x.md"),
        SkillError,
        contains="绑定",
    )


def t_write_text_硬覆盖() -> None:
    section("3.4 fs.write_text 写入 + 硬覆盖")
    fresh_data_dir()
    c = make_bound_course()
    r1 = service.fs_write_text(c.id, "大纲", "outline.md", "# v1\n内容 1")
    expect("首次写入 ok", r1.get("written") is True)
    expect("首次 size > 0", r1["size"] > 0)

    time.sleep(0.05)
    r2 = service.fs_write_text(c.id, "大纲", "outline.md", "# v2\n内容 2 完全不同")
    expect("二次写入 size 改变", r2["size"] != r1["size"])
    expect("二次 sha 与首次不同", r2["sha256"] != r1["sha256"])

    # 读回来应为 v2
    out = service.fs_read_file(c.id, "大纲", "outline.md")
    expect("读回是 v2", "v2" in out["content"])
    expect("读回不再有 v1", "v1" not in out["content"])


def t_二进制后缀拒绝() -> None:
    section("3.5 fs.write_text 拒绝二进制后缀")
    fresh_data_dir()
    c = make_bound_course()
    expect_raises(
        ".pptx 拒",
        lambda: service.fs_write_text(c.id, "幻灯片", "ch1.pptx", "binary data"),
        SkillError,
        contains="二进制",
    )
    expect_raises(
        ".pdf 拒",
        lambda: service.fs_write_text(c.id, "资料", "doc.pdf", "binary"),
        SkillError,
        contains="二进制",
    )


def t_历史记录() -> None:
    section("3.6 fs.file_history 多版本")
    fresh_data_dir()
    c = make_bound_course()
    service.fs_write_text(c.id, "大纲", "outline.md", "v1")
    time.sleep(0.05)
    service.fs_write_text(c.id, "大纲", "outline.md", "v2")
    time.sleep(0.05)
    service.fs_write_text(c.id, "大纲", "outline.md", "v3")

    hist = service.fs_file_history(c.id, "大纲", "outline.md")
    expect("至少 3 个版本", hist["versions"] >= 3)
    expect("current 非空", hist["current"] is not None)
    # 至少 1 个 superseded 行
    superseded = [h for h in hist["history"] if not h["is_current"]]
    expect("至少有 2 个历史版本", len(superseded) >= 2)
    expect("历史版本 superseded_at 非空", all(h["superseded_at"] for h in superseded))


def t_大文件拒绝() -> None:
    section("3.7 20MB 上限")
    fresh_data_dir()
    c = make_bound_course()
    service._set_max_file_bytes(1024)  # 1KB 阈值
    big_text = "A" * 2048
    try:
        service.fs_write_text(c.id, "大纲", "huge.md", big_text)
        expect("写入 > 1KB 应被拒", False, "未抛错")
    except SkillError as e:
        expect("写入 code=file_too_large", e.code == "file_too_large")
    # 写入合法大小
    small = "ok"
    r = service.fs_write_text(c.id, "大纲", "ok.md", small)
    expect("小文件写入 ok", r.get("written") is True)
    # 读大文件
    p = settings.data_dir / "教学" / "数学分析" / "大纲" / "big.md"
    p.write_text(big_text, encoding="utf-8")
    try:
        service.fs_read_file(c.id, "大纲", "big.md")
        expect("读取 > 1KB 应被拒", False, "未抛错")
    except SkillError as e:
        expect("读取 code=file_too_large", e.code == "file_too_large")
    service._set_max_file_bytes(20 * 1024 * 1024)  # 恢复


def t_自动创建目录() -> None:
    section("3.8 fs.write_text 自动创建缺失的子目录")
    fresh_data_dir()
    c = make_bound_course()
    # 删除某个角色目录再写
    import shutil as _sh
    target = settings.data_dir / "教学" / "数学分析" / "提交"
    if target.exists():
        _sh.rmtree(target)
    r = service.fs_write_text(c.id, "提交", "first.txt", "homework submission")
    expect("缺失目录自动创建 + 写入", r.get("written") is True)
    expect("目录已建", target.is_dir())


def t_role_中文与英文() -> None:
    section("3.9 fs.read_file role 接受中文或英文")
    fresh_data_dir()
    c = make_bound_course()
    p = settings.data_dir / "教学" / "数学分析" / "大纲" / "x.md"
    p.write_text("x", encoding="utf-8")
    out1 = service.fs_read_file(c.id, "大纲", "x.md")
    out2 = service.fs_read_file(c.id, "outline", "x.md")
    expect("中文 role OK", out1["content"] == "x")
    expect("英文 role OK", out2["content"] == "x")


def main() -> int:
    sections = [
        t_文本读取,
        t_二进制读取,
        t_路径与不存在,
        t_write_text_硬覆盖,
        t_二进制后缀拒绝,
        t_历史记录,
        t_大文件拒绝,
        t_自动创建目录,
        t_role_中文与英文,
    ]
    fails = 0
    for fn in sections:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"  {_Color.FAIL}FAIL{_Color.END} {fn.__name__} 抛异常：{e}")
            import traceback
            traceback.print_exc()
    print(f"\n==== 阶段 ③ 测试汇总 ====")
    print(f"通过 {_passes}")
    if _failures or fails:
        print(f"失败 {len(_failures) + fails}")
        for s, m in _failures:
            print(f"  [{s}] {m}")
    else:
        print("全部通过")
    cleanup()
    return 0 if not _failures and not fails else 1


if __name__ == "__main__":
    sys.exit(main())