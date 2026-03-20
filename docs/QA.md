# 质量校验记录

> ⚠️ 本文档为上游 MiroThinker 项目遗留的评测 QA 材料，**不属于 OpenClaw-MiroSearch 联网检索主路径**。
> 保留仅供历史参考；当前项目的代码质量检查请参阅 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 上游评测：GAIA-Text-103 子集提取

如果已完成 GAIA-Validation 评测，可用以下脚本提取并重评 GAIA-Text-103 子集：

```bash
# 1. 提取子集
uv run benchmarks/subset_extraction/gaia-to-text-103-mover.py <evaluation_dir>

# 2. 重评
uv run benchmarks/subset_extraction/gaia-text-103-grader.py <extraction_dir>

# 3. 检查结果
uv run benchmarks/check_progress/check_progress_gaia-validation-text-103.py <extraction_dir>
```

## 上游评测：判断模型选择

上游标准化使用 GPT-4.1-2025-04-14 作为主要判断模型，原因：

- 无需自建 GPU 密集型推理服务
- 与 SimpleQA、BrowseComp 等评测基准对齐
- 提供可复现的跨评测比较基线

## 已知问题

- 总结前的上下文管理组件的长度估算精度有待提升，可能影响最终准确性
