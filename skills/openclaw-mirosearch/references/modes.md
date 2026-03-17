# 模式与检索路由选择

## `mode`（研究策略）

- `production-web`：生产网页预设，偏稳态
- `verified`：多轮校验优先，适合事实核查
- `research`：质量优先，成本/耗时更高
- `balanced`：默认推荐，质量/速度/额度平衡
- `quota`：额度优先，事实完整性下降
- `thinking`：纯思考问答，不走工具

## `search_profile`（检索路由）

- `searxng-first`：SearXNG 优先，失败再回退
- `serp-first`：SerpAPI 优先
- `multi-route`：串行多路聚合
- `parallel`：并发聚合
- `parallel-trusted`：并发 + 置信度不足触发高信源串行补检
- `searxng-only`：仅 SearXNG

## 推荐搭配

- 常规检索：`balanced + parallel-trusted`
- 敏感事实核查：`verified + parallel-trusted`
- 成本压缩：`quota + searxng-only`

## 检索深度参数

- `search_result_num`：单轮检索条数（10/20/30）
- `verification_min_search_rounds`：最少检索轮次（仅 `verified` 生效）

推荐：

- 常规：`search_result_num=20`、`verification_min_search_rounds=3`
- 严格核查：`search_result_num=30`、`verification_min_search_rounds=4`

## 什么时候升级到 `verified`

出现以下任一情况：

- 问题包含“截至今天/最新/统计数字/伤亡/军情/金融数据”等高风险口径
- 单次检索结果来源集中且口径冲突
- 用户明确要求“交叉验证/高置信来源”
