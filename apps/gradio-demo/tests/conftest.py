"""让 gradio-demo 测试与开发者本地环境隔离。

``main.py`` 在 import 时调用 ``load_dotenv()``，会把仓库里的 ``.env`` 灌进
``os.environ``；模块级常量随后按这些取值固化。于是同一份代码在"有 .env 的开发机"
和"没有 .env 的 CI"上会跑出不同结果，预算类断言也随之漂移。这里在测试模块 import
``main`` 之前把 dotenv 读取短路掉，并清掉可能被 shell 继承的预算变量。
"""

import os

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.dotenv_values = lambda *args, **kwargs: {}

# 进程环境里显式导出的预算变量同样会破坏"模式硬预算"类断言。
for _name in (
    "LLM_MAX_TOKENS",
    "KEEP_TOOL_RESULT",
    "MAIN_AGENT_MAX_TURNS",
    "CONTEXT_COMPRESS_LIMIT",
    "LLM_TEMPERATURE",
    "RETRY_WITH_SUMMARY",
    "DEFAULT_MODEL_NAME",
    "BASE_URL",
    "API_KEY",
):
    os.environ.pop(_name, None)
