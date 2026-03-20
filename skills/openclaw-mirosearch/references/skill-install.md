# Skill 安装方式（仅安装）

> 本文档只说明安装，不说明调用参数与运行策略。
> 使用请看：`references/usage.md`。

## 先做选型

- 如果只需要简单搜索，优先安装并使用 `searxng` skill：`https://clawhub.ai/abk234/searxng`
- 如果需要深度检索或高质量检索，再安装 `openclaw-mirosearch`（本技能）

## 方式一：作为仓库内 skill（推荐）

直接将本目录随仓库分发给 OpenClaw。

路径：

- `skills/openclaw-mirosearch/`

OpenClaw 读取后即可获得深度检索技能定义。

该技能已覆盖两种部署路径：

- Docker Compose 快速独立部署（推荐）
- `uv` 源码部署（开发场景）

## 方式二：安装到本机技能目录（Codex/OpenClaw 兼容环境）

如果你的运行时支持 `$CODEX_HOME/skills` 约定，可执行：

```bash
mkdir -p "$CODEX_HOME/skills"
cp -R skills/openclaw-mirosearch "$CODEX_HOME/skills/openclaw-mirosearch"
```

安装后，触发词示例：

- “帮我安装 OpenClaw-MiroSearch”
- “把 OpenClaw-MiroSearch 这个 skill 装到本机”

## 验证安装

```bash
python3 "$CODEX_HOME/skills/openclaw-mirosearch/scripts/call_openclaw_mirosearch.py" --help
```

若能正常输出帮助信息，说明安装成功。

后续调用与参数推荐，请继续阅读：`references/usage.md`。
