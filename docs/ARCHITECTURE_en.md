# Architecture Overview

> 📄 中文版：[ARCHITECTURE.md](./ARCHITECTURE.md)

This document describes the overall architecture and module responsibilities of OpenClaw-MiroSearch.

## System Architecture

```mermaid
graph TB
    subgraph User Layer
        WebUI["Web UI<br/>(Gradio Demo)"]
        API["HTTP API<br/>(SSE Streaming)"]
        Agent["Upstream AI Agent<br/>(OpenClaw / Third-party)"]
    end

    subgraph Application Layer["apps/gradio-demo"]
        GradioApp["Gradio App<br/>main.py"]
        ModeRouter["Mode Router<br/>MODE_OVERRIDE_MAP"]
        ConfigLoader["Hydra Config Loader"]
    end

    subgraph Core Layer["apps/miroflow-agent"]
        Pipeline["Task Pipeline<br/>pipeline.py"]
        AgentCore["Agent Core<br/>Main/Sub Agent Loop"]
        ToolExec["Tool Executor<br/>tool_executor.py"]
        AnswerGen["Answer Generator<br/>answer_generator.py"]
        Settings["Config Center<br/>settings.py"]
    end

    subgraph Tool Layer["libs/miroflow-tools"]
        ToolMgr["ToolManager<br/>MCP Client Manager"]
        subgraph MCP_Servers["MCP Servers"]
            Search["search_and_scrape_webpage<br/>Multi-source + Confidence"]
            GoogleSearch["tool-google-search"]
            SogouSearch["tool-sogou-search"]
            VQA["tool-vqa / tool-vqa-os"]
            Audio["tool-transcribe / tool-transcribe-os"]
            Reading["tool-reading"]
            Reasoning["tool-reasoning / tool-reasoning-os"]
            Python["tool-python / stateless_python"]
            Planner["task_planner"]
        end
    end

    subgraph External Services
        SearXNG["SearXNG<br/>(Self-hosted)"]
        SerpAPI["SerpAPI"]
        Serper["Serper"]
        LLM["LLM Gateway<br/>(OpenAI / Anthropic / Qwen)"]
        Jina["Jina Reader"]
    end

    WebUI --> GradioApp
    API --> GradioApp
    Agent --> API

    GradioApp --> ModeRouter
    ModeRouter --> ConfigLoader
    ConfigLoader --> Pipeline

    Pipeline --> AgentCore
    AgentCore --> ToolExec
    AgentCore --> AnswerGen
    Pipeline --> Settings

    ToolExec --> ToolMgr
    ToolMgr --> MCP_Servers

    Search --> SearXNG
    Search --> SerpAPI
    Search --> Serper
    GoogleSearch --> Serper
    GoogleSearch --> Jina
    SogouSearch --> Jina
    AgentCore --> LLM
    AnswerGen --> LLM
```

## Module Responsibilities

### `apps/gradio-demo` — Web UI & API Entry

- Gradio-based Web UI that also exposes SSE streaming API
- Mode routing: maps `mode` (balanced / verified / research, etc.) to Hydra config overrides
- Manages search history (browser localStorage), skill package downloads, runtime observability (stage heartbeat)
- Stale task reconciliation thread: auto-converges long-stale `running` tasks to `failed`

### `apps/miroflow-agent` — Agent Core

- Hydra config system: `conf/agent/*.yaml` defines agent behavior (tool sets, max turns, blacklists, etc.)
- Main agent loop: receive query → tool calls → LLM reasoning → answer generation
- Sub-agent support (e.g., browsing agent), exposed as tools via `expose_sub_agents_as_tools`
- Config center `settings.py`: centralized loading of all environment variables and MCP Server parameters

### `libs/miroflow-tools` — Shared Tool Framework

- `ToolManager`: MCP client lifecycle management with concurrent tool call support
- MCP Servers: each tool runs as an independent stdio process, communicating via MCP protocol
- Core retrieval tool `search_and_scrape_webpage`: multi-source parallel search, confidence evaluation, high-trust supplemental retrieval

### External Service Dependencies

| Service | Purpose | Required |
|---------|---------|----------|
| SearXNG | Self-hosted search aggregation | Recommended (free) |
| SerpAPI / Serper | Commercial search API | At least one search source |
| LLM Gateway | Reasoning & generation | Required |
| Jina Reader | Web scraping & parsing | Recommended |

## Data Flow

```mermaid
sequenceDiagram
    participant User as User / Agent
    participant Demo as Gradio Demo
    participant Pipeline as Task Pipeline
    participant Agent as Agent Core
    participant Tools as MCP Tools
    participant Search as Search Services
    participant LLM as LLM Gateway

    User->>Demo: POST /gradio_api/call/run_research_once
    Demo->>Demo: Mode routing → Hydra config
    Demo->>Pipeline: execute_task_pipeline()
    
    loop Agent Loop (max_turns)
        Pipeline->>Agent: Execute one turn
        Agent->>LLM: Reasoning request
        LLM-->>Agent: Tool call instruction
        Agent->>Tools: execute_tool_call()
        Tools->>Search: Multi-source parallel search
        Search-->>Tools: Search results
        Tools-->>Agent: Tool response
        Agent->>LLM: Reasoning with tool results
        LLM-->>Agent: Next instruction / Final answer
    end
    
    Agent->>Pipeline: Final result
    Pipeline-->>Demo: SSE streaming output
    Demo-->>User: event: complete + Markdown
```

## Deployment Topology

```mermaid
graph LR
    subgraph Docker Compose
        App["app<br/>(Gradio + Agent)"]
        SearXNG["SearXNG"]
        Valkey["Valkey<br/>(Redis-compatible)"]
    end

    App -->|HTTP| SearXNG
    SearXNG -->|Cache| Valkey
    
    ExternalLLM["External LLM API"] -.->|HTTPS| App
    ExternalSearch["SerpAPI / Serper"] -.->|HTTPS| App
    User["User"] -->|HTTP :8080| App
```
