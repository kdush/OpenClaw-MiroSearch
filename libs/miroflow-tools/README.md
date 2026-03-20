# 🛠️ MiroFlow Tools (OpenClaw-MiroSearch)

> A comprehensive tool management system and MCP (Model Context Protocol) server collection for OpenClaw-MiroSearch, providing a unified interface to various AI capabilities including code execution, vision processing, audio transcription, web searching, reasoning, and document reading.

## ✨ Features

- **🔧 Unified Tool Management**: Centralized `ToolManager` for managing multiple MCP servers
- **🌐 Multiple Transport Protocols**: Support for both stdio and SSE (HTTP) connections
- **📦 Rich Tool Ecosystem**: Pre-built MCP servers for common AI tasks
- **⚙️ Flexible Configuration**: Tool blacklisting, timeout management, and custom server configurations
- **🛡️ Error Handling**: Robust retry logic and fallback mechanisms

## 📦 Installation

This package is a local dependency that is automatically installed when you run `uv sync` in the `apps/miroflow-agent` directory. No separate installation is required.

For standalone usage or development:

```bash
cd libs/miroflow-tools
uv sync
```

## 📋 MCP Servers Overview

Quick reference tables of all available MCP servers and their tools. Click on "Details" to jump to the full documentation.

### 📊 Tools Used in Current Default Retrieval Flow

The following tools are used in the current default retrieval flow:

| Category                   | Server Name                 | Tools                                                                                                                | Key Environment Variables                                                                 | Link                                     |
|----------------------------|-----------------------------|----------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------|------------------------------------------|
| **Execution Environment**  | `tool-python`               | `create_sandbox`, `run_command`, `run_python_code`                                                                   | `E2B_API_KEY`, `LOGS_DIR`                                                                 | [Details](#tool-python)                  |
| **File Management**        | `tool-python`               | `upload_file_from_local_to_sandbox`, `download_file_from_sandbox_to_local`, `download_file_from_internet_to_sandbox` | `E2B_API_KEY`, `LOGS_DIR`                                                                 | [Details](#tool-python)                  |
| **Information Retrieval**  | `search_and_scrape_webpage` | `google_search`                                                                                                      | `SERPER_API_KEY`, `SERPER_BASE_URL`                                                        | [Details](#search_and_scrape_webpage)    |
| **Information Retrieval**  | `jina_scrape_llm_summary`   | `scrape_and_extract_info`                                                                                            | `JINA_API_KEY`, `JINA_BASE_URL`, `SUMMARY_LLM_BASE_URL`, `SUMMARY_LLM_MODEL_NAME`, `SUMMARY_LLM_API_KEY` | [Details](#jina_scrape_llm_summary)      |

### 🔧 Additional Available Tools

The following tools are implemented and can be enabled based on your deployment requirements:

| Category                    | Server Name          | Tools                                             | Key Environment Variables                                           | Link                           |
|-----------------------------|----------------------|---------------------------------------------------|---------------------------------------------------------------------|--------------------------------|
| **Web Searching**           | `tool-google-search` | `google_search`, `scrape_website`                 | `SERPER_API_KEY`, `SERPER_BASE_URL`, `JINA_API_KEY`, `JINA_BASE_URL` | [Details](#tool-google-search) |
| **Web Searching (Sogou)**  | `tool-sogou-search` | `sogou_search`, `scrape_website`                 | `TENCENTCLOUD_SECRET_ID`, `TENCENTCLOUD_SECRET_KEY`, `JINA_API_KEY`, `JINA_BASE_URL` | [Details](#tool-sogou-search) |
| **Vision Processing**       | `tool-vqa`           | `visual_question_answering`                       | `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`                            | [Details](#tool-vqa)           |
| **Vision Processing**       | `tool-vqa-os`        | `visual_question_answering`                       | `VISION_API_KEY`, `VISION_BASE_URL`, `VISION_MODEL_NAME`            | [Details](#tool-vqa-os)        |
| **Audio Processing**        | `tool-transcribe`    | `audio_transcription`, `audio_question_answering` | `OPENAI_API_KEY`, `OPENAI_BASE_URL`                                  | [Details](#tool-transcribe)    |
| **Audio Processing**        | `tool-transcribe-os` | `audio_transcription`                             | `WHISPER_API_KEY`, `WHISPER_BASE_URL`, `WHISPER_MODEL_NAME`         | [Details](#tool-transcribe-os) |
| **Document Reading**        | `tool-reading`       | `convert_to_markdown`                             | None required                                                       | [Details](#tool-reading)       |
| **Reasoning Engine**        | `tool-reasoning`     | `reasoning`                                       | `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`                            | [Details](#tool-reasoning)     |
| **Reasoning Engine**        | `tool-reasoning-os`  | `reasoning`                                       | `REASONING_API_KEY`, `REASONING_BASE_URL`, `REASONING_MODEL_NAME`   | [Details](#tool-reasoning-os)  |

## 🚀 Quick Start

<details>
<summary>Click to expand code example</summary>

```python
import asyncio
from miroflow_tools import ToolManager
from mcp import StdioServerParameters

async def main():
    # Initialize tool manager with server configurations
    server_configs = [
        {
            "name": "tool-python",
            "params": StdioServerParameters(
                command="python",
                args=["-m", "miroflow_tools.mcp_servers.python_mcp_server"],
                env={"E2B_API_KEY": "your_e2b_api_key"}  # Required for Python execution
            )
        },
        # Add more server configurations...
    ]

    tool_manager = ToolManager(server_configs)

    # Get all available tool definitions
    tool_definitions = await tool_manager.get_all_tool_definitions()

    # Create a sandbox first
    sandbox_result = await tool_manager.execute_tool_call(
        server_name="tool-python",
        tool_name="create_sandbox",
        arguments={"timeout": 600}
    )

    # Extract sandbox_id from result
    sandbox_id = sandbox_result['result'].split('sandbox_id:')[-1].strip()

    # Execute a tool call
    result = await tool_manager.execute_tool_call(
    server_name="tool-python",
    tool_name="run_python_code",
        arguments={"code_block": "print('Hello, World!')", "sandbox_id": sandbox_id}
    )
    print(result)

if __name__ == "__main__":
    asyncio.run(main())
```

</details>

## 🔧 ToolManager

The `ToolManager` class is the central component for managing and executing tools across multiple MCP servers.

### Key Features

- **🔌 Multi-Server Support**: Manage tools from multiple MCP servers simultaneously
- **🔗 Connection Management**: Automatic connection handling for stdio and SSE transports
- **🚫 Tool Blacklisting**: Filter out specific tools from specific servers
- **📝 Structured Logging**: Optional task logging integration
- **🔄 Error Recovery**: Automatic retry logic and fallback mechanisms

### Methods

- `get_all_tool_definitions()`: Retrieve tool schemas from all configured servers
- `execute_tool_call(server_name, tool_name, arguments)`: Execute a specific tool
- `set_task_log(task_log)`: Enable structured logging
- `get_server_params(server_name)`: Get configuration for a specific server

### Example Usage

<details>
<summary>Click to expand code example</summary>

```python
import asyncio
from miroflow_tools import ToolManager
from mcp import StdioServerParameters

async def main():
    # Configure servers
    server_configs = [
        {
            "name": "python-server",
            "params": StdioServerParameters(
                command="python",
                args=["-m", "miroflow_tools.mcp_servers.python_mcp_server"],
                env={"E2B_API_KEY": "your_key"}
            )
        }
    ]

    # Initialize with optional blacklist
    tool_blacklist = {("python-server", "some_tool")}
    manager = ToolManager(server_configs, tool_blacklist=tool_blacklist)

    # Enable logging
    # manager.set_task_log(your_task_logger)

    # Get tools
    tools = await manager.get_all_tool_definitions()

    # Create a sandbox first (required before running code)
    sandbox_result = await manager.execute_tool_call(
        server_name="python-server",
        tool_name="create_sandbox",
        arguments={"timeout": 600}
    )
    sandbox_id = sandbox_result['result'].split('sandbox_id:')[-1].strip()

    # Execute tool
    result = await manager.execute_tool_call(
        server_name="python-server",
        tool_name="run_python_code",
        arguments={"code_block": "1 + 1", "sandbox_id": sandbox_id}
    )
    print(result)

if __name__ == "__main__":
    asyncio.run(main())
```

</details>

## 🔌 MCP Servers

### Server: tool-python

Execute Python code in isolated E2B sandboxes with persistent sessions.

**Tools**:

- 🔨 `create_sandbox(timeout=600)`: Create a new Linux sandbox
- 🐍 `run_python_code(code_block, sandbox_id)`: Execute Python code
- 💻 `run_command(command, sandbox_id)`: Run shell commands
- ⬆️ `upload_file_from_local_to_sandbox(sandbox_id, local_file_path, sandbox_file_path)`: Upload files
- ⬇️ `download_file_from_internet_to_sandbox(sandbox_id, url, sandbox_file_path)`: Download files
- 💾 `download_file_from_sandbox_to_local(sandbox_id, sandbox_file_path, local_filename)`: Download files

**Environment Variables**:

- 🔑 `E2B_API_KEY`: E2B API key (required)
- 📁 `LOGS_DIR`: Directory for temporary files (default: `../../logs`)

**Example**:

<details>
<summary>Click to expand code example</summary>

```python
import asyncio
from miroflow_tools import ToolManager
from mcp import StdioServerParameters

async def main():
    # Configure server with environment variables
    server_configs = [
        {
            "name": "tool-python",
            "params": StdioServerParameters(
                command="python",
                args=["-m", "miroflow_tools.mcp_servers.python_mcp_server"],
                env={"E2B_API_KEY": "your_e2b_api_key"}
            )
        }
    ]

    manager = ToolManager(server_configs)

    # Create sandbox
    result = await manager.execute_tool_call(
        server_name="tool-python",
        tool_name="create_sandbox",
        arguments={"timeout": 600}
    )

    # Extract sandbox_id from result
    sandbox_id = result['result'].split('sandbox_id:')[-1].strip()

    # Run code
    result = await manager.execute_tool_call(
        server_name="tool-python",
        tool_name="run_python_code",
        arguments={"code_block": "import numpy as np; print(np.array([1,2,3]))", "sandbox_id": sandbox_id}
    )
    print(result)

if __name__ == "__main__":
    asyncio.run(main())
```

</details>

### Server: tool-vqa

Analyze images and answer questions about visual content using Anthropic Claude.

- 👁️ `visual_question_answering(image_path_or_url, question)`
- **Env**: `ANTHROPIC_API_KEY` (required), `ANTHROPIC_BASE_URL` (default: `https://api.anthropic.com`)
- **Module**: `miroflow_tools.mcp_servers.vision_mcp_server`

### Server: tool-vqa-os

Open-source alternative for visual question answering.

- 👁️ `visual_question_answering(image_path_or_url, question)`
- **Env**: `VISION_API_KEY`, `VISION_BASE_URL`, `VISION_MODEL_NAME` (all required)
- **Module**: `miroflow_tools.mcp_servers.vision_mcp_server_os`

### Server: tool-transcribe

Transcribe audio files and answer questions about audio content using OpenAI Whisper.

- 🎤 `audio_transcription(audio_path_or_url)`
- 🎧 `audio_question_answering(audio_path_or_url, question)`
- **Env**: `OPENAI_API_KEY` (required), `OPENAI_BASE_URL` (default: `https://api.openai.com/v1`)
- **Formats**: MP3, WAV, M4A, AAC, OGG, FLAC
- **Module**: `miroflow_tools.mcp_servers.audio_mcp_server`

### Server: tool-transcribe-os

Open-source alternative for audio transcription.

- 🎤 `audio_transcription(audio_path_or_url)`
- **Env**: `WHISPER_API_KEY`, `WHISPER_BASE_URL`, `WHISPER_MODEL_NAME` (all required)
- **Formats**: MP3, WAV, M4A, AAC, OGG, FLAC
- **Module**: `miroflow_tools.mcp_servers.audio_mcp_server_os`

### Server: tool-reading

Convert various document formats to Markdown using MarkItDown.

- 📄 `convert_to_markdown(uri)`: URI must start with `file:`, `data:`, `http:`, or `https:` scheme
- **Env**: None required
- **Formats**: PDF, DOC, DOCX, PPT, PPTX, XLS, XLSX, CSV, ZIP, and more
- **Module**: `miroflow_tools.mcp_servers.reading_mcp_server`

### Server: tool-reasoning

Solve complex reasoning problems using Anthropic Claude with chain-of-thought.

- 🧠 `reasoning(question)`
- **Env**: `ANTHROPIC_API_KEY` (required), `ANTHROPIC_BASE_URL` (default: `https://api.anthropic.com`)
- **Module**: `miroflow_tools.mcp_servers.reasoning_mcp_server`

### Server: tool-reasoning-os

Open-source alternative for complex reasoning.

- 🧠 `reasoning(question)`
- **Env**: `REASONING_API_KEY`, `REASONING_BASE_URL`, `REASONING_MODEL_NAME` (all required)
- **Module**: `miroflow_tools.mcp_servers.reasoning_mcp_server_os`

### Server: search_and_scrape_webpage

Google/Meta search gateway with multi-provider routing support (Serper / SerpAPI / SearXNG).

- 🔍 `google_search(q, gl, hl, location, num, tbs, page, autocorrect)`: Perform web searches and retrieve rich results
- **Env**: `SERPER_API_KEY` (required), `SERPER_BASE_URL` (default: `https://google.serper.dev`)
- **Module**: `miroflow_tools.dev_mcp_servers.search_and_scrape_webpage`

### Server: jina_scrape_llm_summary

Scrape content from URLs and extract meaningful information using an LLM.

- 🔎 `scrape_and_extract_info(url, info_to_extract, custom_headers)`: Scrape and summarize web pages, PDFs, code files, etc.
- **Env**: `JINA_API_KEY` (required), `JINA_BASE_URL`, `SUMMARY_LLM_BASE_URL`, `SUMMARY_LLM_MODEL_NAME`, `SUMMARY_LLM_API_KEY`
- **Module**: `miroflow_tools.dev_mcp_servers.jina_scrape_llm_summary`

### Server: tool-google-search

Google search via Serper API with website scraping capabilities.

- 🔍 `google_search(q, gl, hl, location, num, tbs, page)`: Google search
- 🌐 `scrape_website(url)`: Scrape website content via Jina.ai
- **Env**: `SERPER_API_KEY`, `SERPER_BASE_URL`, `JINA_API_KEY`, `JINA_BASE_URL`
- **Filtering Env** (optional): `REMOVE_SNIPPETS`, `REMOVE_KNOWLEDGE_GRAPH`, `REMOVE_ANSWER_BOX`
- **Module**: `miroflow_tools.mcp_servers.searching_google_mcp_server`

### Server: tool-sogou-search

Sogou search (optimized for Chinese) with website scraping capabilities. *Optional capability*

- 🔍 `sogou_search(Query, Cnt)`: Sogou search (Chinese)
- 🌐 `scrape_website(url)`: Scrape website content via Jina.ai
- **Env**: `TENCENTCLOUD_SECRET_ID`, `TENCENTCLOUD_SECRET_KEY`, `JINA_API_KEY`, `JINA_BASE_URL`
- **Module**: `miroflow_tools.mcp_servers.searching_sogou_mcp_server`

## 🚀 Development

### Adding a New MCP Server

1. Create a new server file in `mcp_servers/`
1. Use `FastMCP` to define tools:
   ```python
   from fastmcp import FastMCP
   mcp = FastMCP("server-name")

   @mcp.tool()
   async def my_tool(arg: str) -> str:
       """Tool description."""
       return "result"

   if __name__ == "__main__":
       mcp.run(transport="stdio")
   ```
1. Add server configuration to your application
1. Update this README with server documentation
