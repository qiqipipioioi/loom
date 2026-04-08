from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PID_FILE = DATA_DIR / "loom.pid"
LOG_FILE = DATA_DIR / "loom.log"
APP_PORT = 8000
EXTRA_PORTS_TO_CLEAR = (8001, 8002)


def is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def is_port_busy(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        PID_FILE.unlink(missing_ok=True)
        return None


def clear_pid_file() -> None:
    PID_FILE.unlink(missing_ok=True)


def wait_for_port(host: str, port: int, expected_state: bool, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_port_busy(host, port) == expected_state:
            return True
        time.sleep(0.2)
    return is_port_busy(host, port) == expected_state


def list_port_pids(port: int) -> list[int]:
    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            pids.append(int(stripped))
    return pids


def terminate_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 5
    while time.time() < deadline:
        if not is_pid_running(pid):
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def clear_port(port: int) -> list[int]:
    pids = [pid for pid in list_port_pids(port) if pid != os.getpid()]
    for pid in pids:
        terminate_pid(pid)
    return pids


def start_app(host: str, port: int) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for extra_port in EXTRA_PORTS_TO_CLEAR:
        released_pids = clear_port(extra_port)
        if released_pids:
            print(f"已清理端口 {extra_port} 的占用进程: {', '.join(map(str, released_pids))}")

    existing_pid = read_pid()
    if existing_pid and is_pid_running(existing_pid):
        print(f"Loom 已在运行，PID={existing_pid}")
        return 0
    clear_pid_file()

    if is_port_busy(host, port):
        released_pids = clear_port(port)
        if released_pids and wait_for_port(host, port, expected_state=False, timeout_seconds=5):
            print(f"已释放 {host}:{port} 占用进程: {', '.join(map(str, released_pids))}")
        if is_port_busy(host, port):
            print(f"{host}:{port} 仍被占用，启动失败。")
            return 1

    with LOG_FILE.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "loom_app.main:app",
                "--host",
                host,
                "--port",
                str(port),
            ],
            cwd=BASE_DIR,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    if wait_for_port(host, port, expected_state=True, timeout_seconds=5):
        print(f"Loom 已启动，PID={process.pid}")
        print(f"访问地址: http://{host}:{port}/")
        print(f"日志文件: {LOG_FILE}")
        return 0

    clear_pid_file()
    print("Loom 启动失败，请检查日志文件。")
    return 1


def stop_app() -> int:
    pid = read_pid()
    if pid is None:
        print("未找到 Loom 的运行记录，无需关闭。")
        return 0

    if not is_pid_running(pid):
        clear_pid_file()
        print("检测到 PID 文件已过期，已自动清理。")
        return 0

    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 5
    while time.time() < deadline:
        if not is_pid_running(pid):
            clear_pid_file()
            print("Loom 已关闭。")
            return 0
        time.sleep(0.2)

    os.kill(pid, signal.SIGKILL)
    clear_pid_file()
    print("Loom 已强制关闭。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loomctl", description="Loom 应用控制命令")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="启动 Loom")
    start_parser.add_argument("--host", default="127.0.0.1")
    start_parser.add_argument("--port", default=APP_PORT, type=int, help="固定使用 8000 端口")

    subparsers.add_parser("stop", help="关闭 Loom")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "start":
        if args.port != APP_PORT:
            print(f"Loom 固定使用 {APP_PORT} 端口，已忽略传入端口 {args.port}。")
        return start_app(host=args.host, port=APP_PORT)
    if args.command == "stop":
        return stop_app()

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
