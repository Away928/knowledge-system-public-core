# Knowledge Delta Protocol — V2

标准顺序：Cognitive Trace → Distiller → Knowledge Delta → Knowledge Manager → Human Review（一次）→ Knowledge Commit。

Distiller 的唯一任务是从 USER 轨迹与当前知识识别真实变化、问题和潜在洞见，不需要生成入库长篇正文；空 Delta batch 有效。

类型：NEW / REFINE / CORRECT / CONNECT / INSIGHT_CANDIDATE / QUESTION / CONFLICT。

- QUESTION 是用户问过的问题，不等于理解或掌握。
- INSIGHT_CANDIDATE 必须有 USER grounding，未经明确确认不得冒充已确认观点。
- 用户、资料与 AI 提供的内容必须保持 USER/SOURCE/AGENT provenance 区分。
- 每项至少包含 delta_id、type、target、before、after、reason、provenance、confidence、trace_evidence、current_knowledge_refs、source_session_ids、review_status、proposed_by。
- Distiller 初始 review_status 为 pending，必须引用 Trace 证据；不得自主 approve。
- Knowledge Manager 负责结合 Vault 形成合并/更新/链接/不入库的最终 Change Set，再提交唯一一次 Human Review。

现有 scripts/distiller.py 的 review() 是 V1 单 Delta approve/reject 接口；迁移到 V2 时不得将该动作解释为 Change Set 审核。FIN3703A 历史批准正文不得被覆盖。
