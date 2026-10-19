"""伴教提醒守护脚本（可选启动）。

- 轮询 banjiao.service.due_soon_homework / due_soon_affairs
- 把"此刻到期"的项目以标准化 JSON 打印到 stdout（一行一项）
- 上层 Agent（Host runtime）应扫描 stdout，按 JSON 解析后转交教师
- 标记已提醒：提醒广播后通过 skill.py affair.mark_reminded 等手动 ack

启动方式（手动）：
    python scripts/reminder_daemon.py --interval 60

停止：Ctrl-C
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from banjiao import service  # noqa: E402

_running = True


def _emit(kind: str, payload: dict) -> None:
    sys.stdout.write(json.dumps(
        {
            "type": kind,
            "emitted_at": datetime.now().isoformat(timespec="seconds"),
            "data": payload,
        },
        ensure_ascii=False,
    ) + "\n")
    sys.stdout.flush()


def _handle_signal(signum, frame):
    global _running
    _running = False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=60, help="轮询秒数（默认 60）")
    args = p.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    sys.stdout.write(json.dumps({"type": "ready", "interval_sec": args.interval}) + "\n")
    sys.stdout.flush()

    # 已提醒标记：基于 id 去重在同一进程内
    seen_affairs: set[str] = set()
    seen_homework: set[str] = set()

    while _running:
        try:
            for a in service.due_soon_affairs():
                if a["id"] not in seen_affairs:
                    seen_affairs.add(a["id"])
                    _emit("affair.due", a)
                    # 自动标记（避免重复推送）；如需手动 ack，关闭 auto_mark
                    try:
                        service.mark_affair_reminded(a["id"])
                    except Exception as e:  # noqa: BLE001
                        sys.stderr.write(f"mark_reminded fail {a['id']}: {e}\n")

            for h in service.due_soon_homework():
                if h["id"] not in seen_homework:
                    seen_homework.add(h["id"])
                    _emit("homework.due", h)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"poll error: {e}\n")

        for _ in range(args.interval):
            if not _running:
                break
            time.sleep(1)

    sys.stdout.write(json.dumps({"type": "stopped"}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
