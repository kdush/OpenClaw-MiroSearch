# 架构概览

> 📄 English version: [ARCHITECTURE_en.md](./ARCHITECTURE_en.md)

本文档描述 OpenClaw-MiroSearch 的整体架构与模块职责。

## 系统架构图

```mermaid
graph TB
    subgraph 用户层
        WebUI["Web UI<br/>(Gradio Demo)"]
        API["HTTP API<br/>(SSE Streaming)"]
        Agent["上层 AI Agent<br/>(OpenClaw / 第三方)"]
    end

    subgraph 应用层["apps/gradio-demo"]
        GradioApp["Gradio 应用<br/>main.py"]
        ModeRouter["模式路由<br/>MODE_OVERRIDE_MAP"]
        ConfigLoader["Hydra 配置加载"]
    end

    subgraph 核心层["apps/miroflow-agent"]
        Pipeline["任务管线<br/>pipeline.py"]
        AgentCore["Agent 核心<br/>主/子 Agent 循环"]
        ToolExec["工具执行器<br/>tool_executor.py"]
        AnswerGen["答案生成器<br/>answer_generator.py"]
        Settings["配置中心<br/>settings.py"]
    end

    subgraph 工具层["libs/miroflow-tools"]
        ToolMgr["ToolManager<br/>MCP 客户端管理"]
        subgraph MCP_Servers["MCP Servers"]
            Search["search_and_scrape_webpage<br/>多源检索 + 置信补检"]
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

    subgraph 外部服务
        SearXNG["SearXNG<br/>(自建)"]
        SerpAPI["SerpAPI"]
        Serper["Serper"]
        LLM["LLM 网关<br/>(OpenAI / Anthropic / Qwen)"]
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

## 模块职责

### `apps/gradio-demo` — Web UI 与 API 入口

- Gradio 构建的 Web 界面，同时暴露 SSE 流式 API
- 负责模式路由：将 `mode`（balanced / verified / research 等）映射为 Hydra 配置覆盖
- 管理搜索历史（浏览器 localStorage）、技能包下载、运行时观测（阶段心跳）
- 陈旧任务巡检线程：自动将长时间未更新的 `running` 任务收敛为 `failed`

### `apps/miroflow-agent` — Agent 核心

- Hydra 配置体系：`conf/agent/*.yaml` 定义 Agent 行为（工具集、最大轮次、黑名单等）
- 主 Agent 循环：接收查询 → 工具调用 → LLM 推理 → 答案生成
- 子 Agent 支持（如 browsing agent），通过 `expose_sub_agents_as_tools` 暴露为工具
- 配置中心 `settings.py`：集中加载所有环境变量与 MCP Server 参数

### `libs/miroflow-tools` — 共享工具框架

- `ToolManager`：MCP 客户端生命周期管理，支持并发工具调用
- MCP Servers：每个工具以独立 stdio 进程运行，通过 MCP 协议通信
- 核心检索工具 `search_and_scrape_webpage`：多源并行检索、置信度评估、高信源补检

### 外部服务依赖

| 服务 | 用途 | 必选 |
|------|------|------|
| SearXNG | 自建搜索聚合 | 推荐（免费） |
| SerpAPI / Serper | 商业搜索 API | 至少配一个搜索源 |
| LLM 网关 | 推理与生成 | 必选 |
| Jina Reader | 网页抓取与解析 | 推荐 |

## 数据流

```mermaid
sequenceDiagram
    participant User as 用户 / Agent
    participant Demo as Gradio Demo
    participant Pipeline as 任务管线
    participant Agent as Agent 核心
    participant Tools as MCP Tools
    participant Search as 检索服务
    participant LLM as LLM 网关

    User->>Demo: POST /gradio_api/call/run_research_once
    Demo->>Demo: 模式路由 → Hydra 配置
    Demo->>Pipeline: execute_task_pipeline()
    
    loop Agent 循环（max_turns）
        Pipeline->>Agent: 执行一轮
        Agent->>LLM: 推理请求
        LLM-->>Agent: 工具调用指令
        Agent->>Tools: execute_tool_call()
        Tools->>Search: 多源并行检索
        Search-->>Tools: 检索结果
        Tools-->>Agent: 工具返回
        Agent->>LLM: 含工具结果的推理
        LLM-->>Agent: 下一步指令 / 最终答案
    end
    
    Agent->>Pipeline: 最终结果
    Pipeline-->>Demo: SSE 流式输出
    Demo-->>User: event: complete + Markdown
```

## 部署拓扑

```mermaid
graph LR
    subgraph Docker Compose
        App["app<br/>(Gradio + Agent)"]
        SearXNG["SearXNG"]
        Valkey["Valkey<br/>(Redis 兼容)"]
    end

    App -->|HTTP| SearXNG
    SearXNG -->|缓存| Valkey
    
    ExternalLLM["外部 LLM API"] -.->|HTTPS| App
    ExternalSearch["SerpAPI / Serper"] -.->|HTTPS| App
    User["用户"] -->|HTTP :8080| App
```
