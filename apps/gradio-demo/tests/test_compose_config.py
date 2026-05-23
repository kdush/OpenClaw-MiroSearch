from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_compose_app_enables_api_backend_mode():
    compose_text = (PROJECT_ROOT / "compose.yaml").read_text()

    assert "BACKEND_MODE: api" in compose_text


def test_host_network_compose_app_enables_api_backend_mode():
    compose_text = (PROJECT_ROOT / "compose.host-network.yaml").read_text()

    assert "BACKEND_MODE: api" in compose_text
