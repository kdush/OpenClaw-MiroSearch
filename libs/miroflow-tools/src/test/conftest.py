"""Shared test bootstrap for miroflow-tools.

``search_and_scrape_webpage`` imports the Tencent Cloud SDK at module level, but
this library does not declare it (the consuming apps do). Install a minimal
stub up front so unit tests can import the module either way.
"""

from __future__ import annotations

import sys
import types


def _install_tencentcloud_stubs() -> None:
    """为缺失的 tencentcloud 依赖注入最小桩，避免单测导入失败。"""
    if "tencentcloud" in sys.modules:
        return

    tencentcloud = types.ModuleType("tencentcloud")
    common = types.ModuleType("tencentcloud.common")
    credential = types.ModuleType("tencentcloud.common.credential")
    common_client = types.ModuleType("tencentcloud.common.common_client")
    exception_pkg = types.ModuleType("tencentcloud.common.exception")
    exception_mod = types.ModuleType(
        "tencentcloud.common.exception.tencent_cloud_sdk_exception"
    )
    profile_pkg = types.ModuleType("tencentcloud.common.profile")
    client_profile = types.ModuleType("tencentcloud.common.profile.client_profile")
    http_profile = types.ModuleType("tencentcloud.common.profile.http_profile")

    class _DummyCredential:
        def __init__(self, *_args, **_kwargs):
            pass

    class _DummyCommonClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def call_json(self, *_args, **_kwargs):
            return {"Response": {}}

    class _DummyTencentCloudSDKException(Exception):
        pass

    class _DummyClientProfile:
        def __init__(self):
            self.httpProfile = None

    class _DummyHttpProfile:
        def __init__(self):
            self.endpoint = ""

    credential.Credential = _DummyCredential
    common_client.CommonClient = _DummyCommonClient
    exception_mod.TencentCloudSDKException = _DummyTencentCloudSDKException
    client_profile.ClientProfile = _DummyClientProfile
    http_profile.HttpProfile = _DummyHttpProfile

    common.credential = credential
    common.common_client = common_client
    common.exception = exception_pkg
    common.profile = profile_pkg
    exception_pkg.tencent_cloud_sdk_exception = exception_mod
    profile_pkg.client_profile = client_profile
    profile_pkg.http_profile = http_profile

    tencentcloud.common = common

    sys.modules["tencentcloud"] = tencentcloud
    sys.modules["tencentcloud.common"] = common
    sys.modules["tencentcloud.common.credential"] = credential
    sys.modules["tencentcloud.common.common_client"] = common_client
    sys.modules["tencentcloud.common.exception"] = exception_pkg
    sys.modules["tencentcloud.common.exception.tencent_cloud_sdk_exception"] = (
        exception_mod
    )
    sys.modules["tencentcloud.common.profile"] = profile_pkg
    sys.modules["tencentcloud.common.profile.client_profile"] = client_profile
    sys.modules["tencentcloud.common.profile.http_profile"] = http_profile


_install_tencentcloud_stubs()
