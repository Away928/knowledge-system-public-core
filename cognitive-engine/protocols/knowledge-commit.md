# Knowledge Commit Protocol — Change Set V2

标准顺序：Distiller → Knowledge Delta → Knowledge Manager → Human Review（一次）→ Knowledge Commit → Obsidian。

Knowledge Commit 负责执行经过 Human Review 明确批准的最终 Change Set，不生成新正文，也不作为第二次人工审核。

1. 读取已批准 Change Set 的 ID、版本/内容哈希、项目级决定、原始 Delta/Trace 和 provenance；拒绝或 pending 项不能写入。
2. 重新读取 Vault 规范、目标页面、首页、关联页面及 Git 状态；检查目标文件原版本哈希、精确 patch 哈希和无冲突。
3. 如出现文件版本漂移、目标变化、事实问题或必须实质改写：暂停受影响项，交还 Knowledge Manager 生成新版本并请求该项的新批准；不能静默改变已批准内容。
4. 只执行批准的最小页面差异；索引、链接、重要日志调整也必须事先体现在 Change Set 中。不得修改 Raw Sources。
5. 保存 USER/SOURCE/AGENT provenance；QUESTION 不得假装为用户已掌握；只在证据充分时陈述确定性事实。
6. 运行 Vault 格式、链接、测试、来源与 Git 差异验证；提交 Git 并保存 Knowledge Commit 审计记录，包括 Change Set、Delta IDs、文件和写入结果。
7. 写入成功之后才根据 Learning Hub 条件更新 Integrated；失败则保留 Pending Integration。

旧 V1 单 Delta 审批不能自动批准不同的精简版页面补丁。允许基于历史记录形成最终迁移 Change Set，经一次性确认后再 Commit。

2026-10-18：Change Set V2 的生成、单次审核历史校验及 guarded Commit CLI 已实现并通过合成 Vault 测试；默认只读，显式 --apply 才可能写 Vault。CLI 不能验证审批者身份；实际用户批准、Vault 检查、Git 提交与 Learning Hub 同步仍由授权操作者分开确认。本轮未执行真实 Vault Commit。
