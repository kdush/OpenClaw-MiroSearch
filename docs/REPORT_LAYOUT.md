# 报告排版规范 / Report Layout

面向 Gradio Web 与导出文件的人类可读终稿结构。

## 必选章节（detailed / deep）

1. **TL;DR / 结论（标明置信度）**
2. **Conclusions / 详细结论**
3. **冲突与不确定 / Conflicts & Uncertainties**
4. **Evidence / 证据**（中英标题均可）
5. **References**（URL 必须完整；不完整链接会被丢弃）

## 推荐增强

- **内容分析 / Content Analysis**：角色、主张、时间线要点
- **关系拓扑 / Relationship Map**：`mermaid` flowchart（主张→证据/反驳→缺口）
- **线索追踪 / Lead Trail**：已跟进线索写发现；未跟进仅摘要为「未跟进」，避免长英文 pending 造成“写到一半”的观感

## 禁止出现在用户终稿

- `Token Usage & Cost` / Pricing 诊断块
- `Extracted Result` 调试分段
- 截断到一半的 URL（如 `https://www`）

实现：`apps/miroflow-agent/src/io/report_presentation.py` 的 `prepare_user_facing_report()`，在编排器写出终稿前调用；Gradio 侧另有诊断剥离兜底。
