# Skill 安装方式

## 方式一：作为仓库内 skill（推荐）

直接将本目录随仓库分发给 OpenClaw。

路径：

- `skills/openclaw-mirosearch/`

OpenClaw 读取后即可获得安装、调用、模式选择、排障知识。

## 方式二：安装到本机技能目录（Codex/OpenClaw 兼容环境）

如果你的运行时支持 `$CODEX_HOME/skills` 约定，可执行：

```bash
mkdir -p "$CODEX_HOME/skills"
cp -R skills/openclaw-mirosearch "$CODEX_HOME/skills/openclaw-mirosearch"
```

安装后，触发词示例：

- “帮我安装 OpenClaw-MiroSearch”
- “用 OpenClaw-MiroSearch API 查这个问题”
- “这个问题应该用哪个 mode 和 search_profile”

## 验证安装

```bash
python3 "$CODEX_HOME/skills/openclaw-mirosearch/scripts/call_openclaw_mirosearch.py" --help
```

若能正常输出帮助信息，说明安装成功。
