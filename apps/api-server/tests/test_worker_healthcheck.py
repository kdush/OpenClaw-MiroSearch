from unittest.mock import MagicMock

import worker_healthcheck as wh


class TestWorkerHealthcheck:
    def test_is_worker_process_running_true(self, monkeypatch):
        monkeypatch.setattr(wh, "_read_proc_cmdline", lambda pid=1: "python worker.py")
        assert wh.is_worker_process_running() is True

    def test_is_worker_process_running_false(self, monkeypatch):
        monkeypatch.setattr(wh, "_read_proc_cmdline", lambda pid=1: "python main.py")
        assert wh.is_worker_process_running() is False

    def test_can_ping_valkey_true(self, monkeypatch):
        client = MagicMock()
        client.ping.return_value = True
        redis_cls = MagicMock(return_value=client)
        monkeypatch.setattr(wh.redis, "Redis", redis_cls)

        assert wh.can_ping_valkey() is True
        client.close.assert_called_once()

    def test_main_returns_zero_when_worker_and_valkey_ok(self, monkeypatch):
        monkeypatch.setattr(wh, "is_worker_process_running", lambda pid=1: True)
        monkeypatch.setattr(wh, "can_ping_valkey", lambda: True)
        assert wh.main() == 0

    def test_main_returns_one_when_worker_missing(self, monkeypatch):
        monkeypatch.setattr(wh, "is_worker_process_running", lambda pid=1: False)
        monkeypatch.setattr(wh, "can_ping_valkey", lambda: True)
        assert wh.main() == 1

    def test_main_returns_one_when_valkey_unreachable(self, monkeypatch):
        monkeypatch.setattr(wh, "is_worker_process_running", lambda pid=1: True)
        monkeypatch.setattr(wh, "can_ping_valkey", lambda: False)
        assert wh.main() == 1
