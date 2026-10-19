"""临时启动伴教网页后端。

打开浏览器访问输出的地址即可查看课表/作业/事务。用 Ctrl-C 关闭进程即可，
数据已持久化到 SQLite，下次需要时再启动。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 把 src/ 加入 sys.path，让 banjiao 包可被直接 import
SKILL_ROOT = Path(__file__).resolve().parent
SRC_DIR = SKILL_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import uvicorn

from banjiao.config import settings  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="临时启动伴教网页后端（Ctrl-C 结束）")
    p.add_argument("--host", default=settings.host)
    p.add_argument("--port", type=int, default=settings.port)
    p.add_argument("--no-reload", action="store_true", help="关闭热重载（默认开启）")
    args = p.parse_args()

    print(f"伴教 临时网页服务已启动 → http://{args.host}:{args.port}")
    print("用 Ctrl-C 结束服务即可；不需要常驻。")
    uvicorn.run(
        "banjiao.api:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
