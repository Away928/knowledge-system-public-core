# Cognitive Trace Protocol

Cognitive Trace 是临时学习轨迹，不是永久笔记，也不是聊天全文。

建议字段：session_id / date / platform / topic；sources；user_questions；user_reasoning；source_claims；ai_contributions；corrections；candidate_insights；open_questions；citations/provenance；status。

原则：
- 只捕获会改变长期知识状态的信息，不保存无价值寒暄和完整逐字聊天。
- user_reasoning 保存用户自己的推理、类比和质疑。
- source_claims 必须能追溯到来源。
- ai_contributions 与用户观点分离。
- candidate_insights 默认不视为用户立场，等待确认。
- status 使用 pending 或 processed。

## Session 边界
- Session = Learning Unit + 连续认知主题链，不等于聊天窗口或一次登录。
- 并行的其他工作/其他对话不切断当前学习 Session。
- 同一 Learning Unit 可以并行存在多个不同主题链的 Session。
- 只有真实认知事件才开启 Session；source 同步、文件导入等准备操作不算学习。
- Cognitive Trace 必须携带 `learning_unit_id`、`session_id`，并在适用时保留 `topic_chain` 与跨平台来源。

## Session Capture V1 事件
V1 允许的认知事件：`USER_QUESTION`、`USER_REASONING`、`USER_CORRECTION`、`USER_CONNECTION`、`USER_CONFIRMATION`、`USER_REJECTION`、`SOURCE_CLAIM`、`AGENT_EXPLANATION`、`AGENT_INFERENCE`、`OPEN_QUESTION`。

- USER 类事件 provenance 固定为 `USER`；SOURCE_CLAIM 固定为 `SOURCE`；Agent 类事件固定为 `AGENT`。
- `AGENT_EXPLANATION` / `AGENT_INFERENCE` 可以进入短期 buffer，但默认不进入最终 Cognitive Trace，除非它们对理解变化具有必要上下文价值而被显式 retain。
- 事件内容应是紧凑的认知事实，不得把整段聊天逐字复制进 buffer。
- `questioned / reasoned / connected / corrected / applied / restated` 等 evidence strength 是证据 metadata，不是掌握度评分。
- Session 的候选匹配可以使用 topic similarity，但最终连续性由语义 Agent 判断；时间不参与硬切分。
