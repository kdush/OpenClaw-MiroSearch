import importlib
import sys
from pathlib import Path

from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_search_and_scrape_webpage_forwards_scrape_proxy_fake_ip_cidrs(monkeypatch):
    monkeypatch.setenv("SCRAPE_PROXY_FAKE_IP_CIDRS", "198.18.0.0/15")
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://searxng:8080")

    settings = importlib.import_module("src.config.settings")
    cfg = OmegaConf.create({})
    agent_cfg = OmegaConf.create(
        {
            "tools": ["search_and_scrape_webpage"],
            "tool_blacklist": [],
        }
    )

    configs, blacklist = settings.create_mcp_server_parameters(cfg, agent_cfg)

    assert blacklist == set()
    search_cfg = next(item for item in configs if item["name"] == "search_and_scrape_webpage")
    assert (
        search_cfg["params"].env["SCRAPE_PROXY_FAKE_IP_CIDRS"] == "198.18.0.0/15"
    )
    assert search_cfg["params"].env["SEARXNG_BASE_URL"] == "http://searxng:8080"
