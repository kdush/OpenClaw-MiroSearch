"""ToolManager 运行时资源清理测试。"""

from unittest.mock import AsyncMock

import pytest

from miroflow_tools.manager import ToolManager


@pytest.mark.asyncio
async def test_aclose_closes_browser_session_once():
    """重复关闭同一 manager 时，浏览器会话只能关闭一次。"""
    manager = ToolManager([])
    browser_session = AsyncMock()
    manager.browser_session = browser_session

    await manager.aclose()
    await manager.aclose()

    browser_session.close.assert_awaited_once_with()
    assert manager.browser_session is None


@pytest.mark.asyncio
async def test_aclose_does_not_retry_failed_browser_session_close():
    """首次关闭抛错后也应解除引用，避免重复关闭同一损坏会话。"""
    manager = ToolManager([])
    browser_session = AsyncMock()
    browser_session.close.side_effect = RuntimeError("close failed")
    manager.browser_session = browser_session

    with pytest.raises(RuntimeError, match="close failed"):
        await manager.aclose()
    await manager.aclose()

    browser_session.close.assert_awaited_once_with()
    assert manager.browser_session is None
