import sys
from pathlib import Path

import redis

from settings import settings


def _read_proc_cmdline(pid: int = 1) -> str:
    return (
        Path(f"/proc/{pid}/cmdline")
        .read_bytes()
        .replace(b"\x00", b" ")
        .decode("utf-8", errors="ignore")
        .strip()
    )


def is_worker_process_running(pid: int = 1) -> bool:
    return "worker.py" in _read_proc_cmdline(pid)


def can_ping_valkey() -> bool:
    client = redis.Redis(
        host=settings.valkey.host,
        port=settings.valkey.port,
        password=settings.valkey.password,
        db=settings.valkey.queue_db,
        decode_responses=True,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
    )
    try:
        return bool(client.ping())
    finally:
        client.close()


def main() -> int:
    try:
        if not is_worker_process_running():
            print("worker process is not running", file=sys.stderr)
            return 1
        if not can_ping_valkey():
            print("valkey ping failed", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:
        print(f"worker healthcheck failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
