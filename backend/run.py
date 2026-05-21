"""
RedMuse 后端启动脚本

在 Windows 下，Playwright 依赖 ProactorEventLoop 来启动 Chromium 子进程。
uvicorn --reload 模式会在子进程中创建 SelectorEventLoop，导致 Playwright 失败。

此脚本通过以下方式解决：
1. 在进程最早期设置 WindowsProactorEventLoopPolicy
2. 使用 uvicorn 编程式启动，确保策略生效
3. 手动实现文件监控 + 进程重启，替代 uvicorn --reload

用法：
    python backend/run.py              # 默认模式（不自动重启，手动重启）
    python backend/run.py --reload     # 开发模式（文件变更自动重启）
"""
import asyncio
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def main():
    import argparse

    parser = argparse.ArgumentParser(description="RedMuse Backend Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--no-reload", action="store_true", help="禁用自动重载", default=True)
    parser.add_argument("--reload", action="store_true", help="启用自动重载（开发模式）")
    args = parser.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.chdir(repo_root)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    import uvicorn

    if args.no_reload and not args.reload:
        uvicorn.run(
            "backend.app.main:app",
            host=args.host,
            port=args.port,
        )
    else:
        _run_with_restart(args.host, args.port)


def _run_with_restart(host: str, port: int):
    """
    使用子进程 + 文件监控实现自动重启。
    每次子进程继承父进程的 ProactorEventLoop 策略。
    """
    import signal
    import subprocess
    import time

    import socket

    watch_dirs = ["backend", "apis", "viral_agent", "xhs_utils"]
    watch_exts = {".py"}

    def _is_port_busy(p: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", p)) == 0

    def _get_mtimes():
        mtimes = {}
        for d in watch_dirs:
            if not os.path.isdir(d):
                continue
            for root, _, files in os.walk(d):
                for f in files:
                    if os.path.splitext(f)[1] in watch_exts:
                        path = os.path.join(root, f)
                        try:
                            mtimes[path] = os.path.getmtime(path)
                        except OSError:
                            pass
        return mtimes

    child = None

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def _start_child():
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import asyncio, os, sys\n"
                    f"repo_root = {repo_root!r}\n"
                    "os.chdir(repo_root)\n"
                    "if repo_root not in sys.path:\n"
                    "    sys.path.insert(0, repo_root)\n"
                    "if sys.platform == 'win32':\n"
                    "    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())\n"
                    "import uvicorn\n"
                    f"uvicorn.run('backend.app.main:app', host='{host}', port={port})\n"
                ),
            ],
        )

    def _shutdown(sig=None, frame=None):
        nonlocal child
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"[RedMuse] Backend dev mode (auto-restart)")
    print(f"  http://{host}:{port}")
    print(f"  Watch dirs: {', '.join(watch_dirs)}")
    print(f"  Ctrl+C to quit\n")

    prev_mtimes = _get_mtimes()
    child = _start_child()

    try:
        while True:
            time.sleep(1.5)

            if child.poll() is not None:
                print(f"\n[!] Server exited (code={child.returncode}), restarting in 3s...")
                time.sleep(3)
                port_busy = _is_port_busy(port)
                if port_busy:
                    print(f"    Port {port} still in use, waiting...")
                    for _ in range(10):
                        time.sleep(2)
                        if not _is_port_busy(port):
                            break
                    else:
                        print(f"    Port {port} stuck. Kill occupying process or use --port N")
                        sys.exit(1)
                prev_mtimes = _get_mtimes()
                child = _start_child()
                continue

            curr_mtimes = _get_mtimes()
            changed = [
                p
                for p in set(list(prev_mtimes.keys()) + list(curr_mtimes.keys()))
                if prev_mtimes.get(p) != curr_mtimes.get(p)
            ]
            if changed:
                short_names = [os.path.relpath(p) for p in changed[:5]]
                extra = f" (+{len(changed) - 5} more)" if len(changed) > 5 else ""
                print(f"\n[*] Changed: {', '.join(short_names)}{extra}")
                print("    Restarting...")
                child.terminate()
                child.wait(timeout=5)
                prev_mtimes = _get_mtimes()
                child = _start_child()
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
