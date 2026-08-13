"""Compose 示例的认证配置契约。"""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_ENV_EXAMPLE = REPOSITORY_ROOT / ".env.compose.example"
API_SERVER_ENV_EXAMPLE = REPOSITORY_ROOT / "apps" / "api-server" / ".env.example"
API_SPEC = REPOSITORY_ROOT / "docs" / "API_SPEC.md"


def _read_simple_env(path: Path) -> dict[str, str]:
    """读取示例文件中的非注释键值；本测试只使用未转义的标量配置。"""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_compose_example_auth_mode_is_self_consistent():
    """API 模式示例必须能启动，同时不能默认暴露到非回环地址。"""
    values = _read_simple_env(COMPOSE_ENV_EXAMPLE)

    assert values["BACKEND_MODE"] == "api"
    assert values["BIND_HOST"] == "127.0.0.1"

    configured_tokens = {
        token.strip() for token in values["API_TOKENS"].split(",") if token.strip()
    }
    bearer_token = values["API_BEARER_TOKEN"].strip()
    if values["AUTH_DISABLED"] == "1":
        assert configured_tokens == set()
        assert bearer_token == ""
    else:
        assert bearer_token
        assert bearer_token in configured_tokens


def test_api_server_env_does_not_trust_proxy_headers_by_default():
    """独立 API 示例必须显式采用安全的反向代理信任默认值。"""
    values = _read_simple_env(API_SERVER_ENV_EXAMPLE)

    assert values["TRUST_PROXY"] == "0"


def test_api_spec_lists_authentication_not_configured_error():
    """API 错误码表必须包含 fail-closed 的认证未配置响应。"""
    contents = API_SPEC.read_text(encoding="utf-8")
    error_table = contents.split("### 错误码", maxsplit=1)[1].split(
        "\n---",
        maxsplit=1,
    )[0]

    assert "| `503` |" in error_table
