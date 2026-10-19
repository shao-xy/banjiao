"""阶段 ① 测试：本地文件系统骨架（路径/绑定/增量扫描/硬删确认）。

新增 action：
    fs.init
    fs.list_top_dirs
    fs.bind_course
    fs.unbind_course
    course.list_files
    fs.scan_course   （增量扫描）
    fs.delete        （带确认）

约束：
    路径必须落在 data/ 子树内，禁止 .. 穿越
    删除有内容文件必须 confirm 字段
    bind 不允许重复；解绑仅断关联，不删文件
    增量扫描：mtime 不变 → 复用上次 sha256
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


# ===== 复用主测试框架 =====

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
            haystack = str(e) + ("\n" + repr(e) if True else "")
            if contains and contains not in haystack:
                expect(label, False, f"抛错但消息不含 {contains!r}：{haystack[:300]}")
                return
            expect(label, True)
            return
        expect(label, False, f"抛出 {type(e).__name__} 而非 {exc_type.__name__}: {e}")
        return
    expect(label, False, "未抛错")


# ===== 隔离数据目录 =====

_TEST_DIR = Path(tempfile.mkdtemp(prefix="banjiao_fs_test_"))
os.environ["BANJIAO_DATA_DIR"] = str(_TEST_DIR / "data")
os.environ.setdefault("BANJIAO_ENABLE_VECTOR", "0")

# 强制隔离加载（关键：在 import service 之前重置模块缓存）
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


# ===== 工具 =====

def fresh_data_dir() -> None:
    """完全重置测试 data 目录（保留文件结构如 _archive）。"""
    # 关闭 db 句柄
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


def make_course(name: str = "数学分析", class_group: str = "2024-A") -> models.Course:
    return service.create_course({"name": name, "class_group": class_group})


def file_in(top_kind: str, course_name: str, role: str, *parts: str) -> Path:
    p = settings.data_dir / top_kind / course_name / role
    p.mkdir(parents=True, exist_ok=True)
    for x in parts:
        p = p / x
    return p


def write_file(p: Path, content: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


# ===== 测试组 =====

def t_路径校验() -> None:
    section("1.1 路径校验（绝对安全）")
    fresh_data_dir()
    service.fs_init()
    c = make_course("数学分析")
    # 先建好物理目录
    (settings.data_dir / "教学" / "数学分析" / "幻灯片").mkdir(parents=True)
    service.fs_bind_course("教学", "数学分析", c.id)

    # 合法路径：通过
    p = file_in("教学", "数学分析", "幻灯片", "第1章.pptx")
    write_file(p, b"hello")
    out = service.fs_scan_course(c.id)
    expect("合法路径扫描成功", any(f["path"] == "教学/数学分析/幻灯片/第1章.pptx" for f in out))

    # 跨出 data/ 的相对路径必须被拒 → SkillError(bad_param)
    expect_raises(
        "禁止 .. 穿越到 data/ 之外",
        lambda: service._safe_path("教学/../../../etc/passwd"),
        SkillError,
        contains="..",
    )
    expect_raises(
        "绝对路径被拒",
        lambda: service._safe_path("/etc/passwd"),
        SkillError,
        contains="绝对路径",
    )
    expect_raises(
        "空路径",
        lambda: service._safe_path(""),
        SkillError,
        contains="不能为空",
    )


def t_顶层目录_init() -> None:
    section("1.2 fs.init 幂等创建 4 个顶层文件夹")
    fresh_data_dir()
    # 初始空
    service.fs_init()
    for kind in ("教学", "科研", "事务", "文件"):
        expect(f"顶层 {kind}/ 存在", (settings.data_dir / kind).is_dir())
    # 二次幂等
    service.fs_init()
    expect("fs.init 幂等", (settings.data_dir / "教学").is_dir())


def t_绑定与解绑() -> None:
    section("1.3 绑定/解绑课程到文件夹")
    fresh_data_dir()
    service.fs_init()
    c1 = make_course("数学分析")
    c2 = make_course("教育学原理")

    # 创建一个未绑定的目录
    top = settings.data_dir / "教学" / "数学分析"
    top.mkdir(parents=True)
    (top / "幻灯片").mkdir()

    # 列表显示孤儿（用 dirname 字段而不是 name）
    orphans = service.fs_list_top_dirs()
    expect("存在孤儿 '数学分析'",
           any(d.get("dirname") == "数学分析" and d.get("top_kind") == "教学" for d in orphans))

    # 绑定
    binding = service.fs_bind_course("教学", "数学分析", c1.id)
    expect("bind 返回 binding_id", isinstance(binding["id"], str))

    # 重复绑定（同名/同 Course）必须被拒
    expect_raises(
        "重复绑定同 Course 被拒",
        lambda: service.fs_bind_course("教学", "数学分析", c1.id),
        SkillError,
        contains="已绑定",
    )

    # 列目录显示绑定的（只看子目录行）
    listing = service.fs_list_top_dirs()
    sub_dirs = [d for d in listing if d.get("top_kind") and d.get("dirname")]
    bound = [d for d in sub_dirs if d.get("binding") is not None]
    expect("绑定后显示 1 条已绑定", len(bound) == 1)
    expect("绑定课程指向 c1", bound[0]["binding"]["course_id"] == c1.id)

    # 解绑：目录与文件保留
    service.fs_unbind_course(binding["id"])
    after = service.fs_list_top_dirs()
    sub_dirs_after = [d for d in after if d.get("top_kind") and d.get("dirname")]
    expect("解绑后无 binding",
           all(d.get("binding") is None for d in sub_dirs_after))
    expect("解绑后目录仍在磁盘",
           (settings.data_dir / "教学" / "数学分析").is_dir())


def t_增量扫描() -> None:
    section("1.4 增量扫描（mtime 不变 → 复用 sha256）")
    fresh_data_dir()
    service.fs_init()
    c = make_course("数学分析")
    # 创建课程的目录结构
    top = settings.data_dir / "教学" / "数学分析"
    top.mkdir(parents=True)
    service.fs_bind_course("教学", "数学分析", c.id)

    p1 = file_in("教学", "数学分析", "幻灯片", "ch1.pptx")
    write_file(p1, b"v1")
    p2 = file_in("教学", "数学分析", "讲义", "intro.md")
    write_file(p2, b"hello world")

    # 第一次扫描
    first = service.fs_scan_course(c.id)
    expect("首次扫描返回 2 个文件", len(first) == 2)
    sha1 = next((f for f in first if f["filename"] == "ch1.pptx"), None)
    expect("ch1.pptx 有 sha256", sha1 is not None and len(sha1["sha256"]) == 64)

    # 第二次扫描不变 → sha256 复用
    second = service.fs_scan_course(c.id)
    sha1b = next((f for f in second if f["filename"] == "ch1.pptx"), None)
    expect("mtime 不变仍得到同样的 sha256", sha1b is not None and sha1b["sha256"] == sha1["sha256"])

    # 改文件 → 新 sha256
    time.sleep(0.05)  # 跨过 mtime 精度
    p1.write_bytes(b"v2-totally-different")
    third = service.fs_scan_course(c.id)
    sha1c = next((f for f in third if f["filename"] == "ch1.pptx"), None)
    expect("改文件后 sha256 改变", sha1c is not None and sha1c["sha256"] != sha1["sha256"])

    # 教师删除文件 → 清单少一项
    p1.unlink()
    fourth = service.fs_scan_course(c.id)
    expect("删除文件后清单少 1", len(fourth) == 1)
    expect("intro.md 仍在", fourth[0]["filename"] == "intro.md")


def t_course_list_files_稳定() -> None:
    section("1.5 course.list_files：与扫描结果一致")
    fresh_data_dir()
    service.fs_init()
    c = make_course("数学分析")
    top = settings.data_dir / "教学" / "数学分析"
    top.mkdir(parents=True)
    service.fs_bind_course("教学", "数学分析", c.id)

    for i in range(3):
        write_file(file_in("教学", "数学分析", "幻灯片", f"s{i}.pptx"), f"slide{i}".encode())
    service.fs_scan_course(c.id)
    listing = service.course_list_files(c.id, role="slides")
    expect("list_files 按 role 过滤",
           len(listing) == 3 and all(f["role"] == "slides" for f in listing))


def t_硬删确认() -> None:
    section("1.6 fs.delete：含内容必须 confirm")
    fresh_data_dir()
    service.fs_init()
    c = make_course("数学分析")
    top = settings.data_dir / "教学" / "数学分析" / "幻灯片"
    top.mkdir(parents=True)
    p = top / "doomed.pptx"
    write_file(p, b"important content")

    # 不带 confirm → 拒绝
    expect_raises(
        "删除非空文件不带 confirm 被拒",
        lambda: service.fs_delete("教学/数学分析/幻灯片/doomed.pptx"),
        SkillError,
        contains="confirm",
    )

    # 错误 confirm → 拒绝
    expect_raises(
        "错误 confirm token 被拒",
        lambda: service.fs_delete(
            "教学/数学分析/幻灯片/doomed.pptx",
            confirm="YES_DELETE_WRONG",
        ),
        SkillError,
        contains="不匹配",
    )

    # 拿到正确 sha8 后允许删除
    full = settings.data_dir / "教学" / "数学分析" / "幻灯片" / "doomed.pptx"
    import hashlib as _h
    sha = _h.sha256(full.read_bytes()).hexdigest()[:8]
    out = service.fs_delete(
        "教学/数学分析/幻灯片/doomed.pptx",
        confirm=f"YES_DELETE_{sha}",
    )
    expect("正确 confirm 后删除成功", not full.exists())


def t_并发安全() -> None:
    section("1.7 并发：扫描无锁死")
    fresh_data_dir()
    service.fs_init()
    c = make_course("数学分析")
    top = settings.data_dir / "教学" / "数学分析"
    top.mkdir(parents=True)
    service.fs_bind_course("教学", "数学分析", c.id)

    for i in range(5):
        write_file(file_in("教学", "数学分析", "幻灯片", f"a{i}.pptx"), b"x" * 10)

    results = []
    errors = []

    def worker():
        try:
            r = service.fs_scan_course(c.id)
            results.append(r)
        except Exception as e:
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    expect("3 个并发扫描全部完成", len(results) == 3 and len(errors) == 0,
           "; ".join(errors))
    expect("每次扫描结果稳定（都是 5 个文件）",
           all(len(r) == 5 for r in results))


def t_teacher_idempotent_init() -> None:
    section("1.8 教师友好：init 在已有非空目录时不破坏内容")
    fresh_data_dir()
    # 提前放一个文件在 data/
    (settings.data_dir / "教学").mkdir(parents=True)
    keep = settings.data_dir / "教学" / "既有的.txt"
    keep.write_text("不要删我", encoding="utf-8")

    service.fs_init()
    expect("init 不破坏已有文件", keep.exists() and keep.read_text(encoding="utf-8") == "不要删我")


# ===== 主 =====

def run_all() -> int:
    sections = [
        t_路径校验,
        t_顶层目录_init,
        t_绑定与解绑,
        t_增量扫描,
        t_course_list_files_稳定,
        t_硬删确认,
        t_并发安全,
        t_teacher_idempotent_init,
    ]
    for fn in sections:
        try:
            fn()
        except Exception:
            traceback.print_exc()
            _failures.append((fn.__name__, "未捕获异常"))

    print()
    total = _passes + len(_failures)
    print(f"==== 阶段 ① 测试汇总 ====")
    print(f"通过 {_passes} / {total}")
    if _failures:
        print(f"{_Color.FAIL}失败 {len(_failures)} 个：{_Color.END}")
        for sec, msg in _failures:
            print(f"  - [{sec}] {msg}")
        return 1
    print(f"{_Color.OK}全部通过{_Color.END}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run_all())
    finally:
        cleanup()
