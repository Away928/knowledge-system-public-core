# Human Review V2 — 单次 Change Set 审批

## 流程与职责

标准流程：Distiller → Knowledge Delta → Knowledge Manager → Human Review（唯一一次）→ Knowledge Commit → Obsidian。

- Distiller 识别 USER 真实提问、认知变化、矛盾或潜在洞见；空 Delta 合法。问题不等于已掌握。
- Knowledge Manager 先读取真实 Vault 与原始证据，验证准确性、provenance、适用边界；去重、拆分或合并 Delta；优先更新已有概念、仅在独立知识主题成立时新建；输出最终页面 Change Set 和只读 Diff。
- Human Review 仅审核 Knowledge Manager 已完成的最终 Change Set，而非未经映射的长篇解释。A 批准、B 修订、C 拒绝；可以批量批准和逐项排除。批准锁定精确目标路径及补丁正文。
- Knowledge Commit 只能执行批准版本，无二次常规审批。若文件版本漂移、事实冲突或需要实质改写，则仅暂停受影响项，回到 Knowledge Manager 生成新补丁并审核，不得静默改动。

## 审批前自动核查

1. 每一项可追溯到原始 Delta ID、Session、Trace Evidence 和 USER/SOURCE/AGENT 身份；引用用户原话必须核对。
2. QUESTION 和未确认的 INSIGHT_CANDIDATE 不得写成用户已经掌握；AI 扩展应如实标注 AGENT。
3. 核对现存页面、实际原始资料、事实/公式/假设、重复/冲突；允许 no-op 与只记录知识缺口。
4. Change Set 的每个变更有 change_id、目标文件、create/update/merge/link/no-op 动作、原文件版本哈希或不存在标记、完整最终 patch/diff、所映射 Delta IDs、来源及风险。
5. 索引、链接、日志中的实质变更须包含在 Change Set。审查前不得写 Vault。

## 唯一人工界面：ChatGPT 内审查最终入库版本（强制）

**审批对象是最终入库的真实文件内容，而不是摘要、知识要点、入库意向或教学解释。** 默认直接在当前 ChatGPT 对话展示，禁止把“请先去本地找文件”作为常规审核入口。后台可保留完整 Diff、来源、哈希与机器文件，供执行器使用；用户不必打开这些文件才能确认内容。

每次先标明 Change Set ID、**当前版本哈希**、状态（pending/approved）、Vault 根目录、变更总数及各目标的 CREATE/UPDATE/DELETE（如适用）操作。逐项审查时执行：

1. **CREATE（新建文件）**：显示目标路径（相对 Vault 根目录）及文件的**全部最终 Markdown**，包含 YAML frontmatter、H1/H2 标题、完整正文、公式、Obsidian 双链、来源与资料定位。不得省略“重复的来源”、文件属性或结尾章节；不得用缩写、概括、表格中的知识要点来代替完整正文。
2. **UPDATE（更新已有文件）**：显示目标路径、准确插入/替换/删除位置、精确的增删改 Diff；在不影响判断的情况下，未变化的旧正文不必重贴。若为重排/大面积改写，需展示修改后完整受影响章节或整页，以免用户无法判断最终呈现。
3. **MOVE/RENAME/DELETE（如支持）**：显示原/新路径、受影响链接与将删除的完整内容或可审查的精确差异；操作必须确实被执行器支持，不能用概括替代。
4. **index.md / 链接 / 元数据**：视为普通受审变更，必须展示准确位置和最终写入文字，不得在用户批准后私自补写。
5. 区分**用户确实提出的问题、USER 洞见、SOURCE 事实与 AGENT 补充**，不要将 QUESTION 视为掌握。来源字段可放在最终 Markdown 中，但要完整展示。
6. 同一个 Change Set 可以在对话中**分多条消息**完整展示；未展示完不得要求批准整包。若篇幅很长，按可审查的文件/章节连续展示，而不是退化成摘要或强迫去打开文件。
7. 审核时不得出现“拟入库知识”“大概写入”等让人误解为摘要的措辞来代替真实正文；可以简短解释调整理由，但明确区分**实际文件文本**与**解释意见**。
8. A = 用户明确批准已经完整展示的**该版本、这些目标与具体补丁**；B = 指定条目修改并重新展示受影响文件/差异（新内容重新锁定哈希，按版本要求再批）；C = 拒绝全部或指定项。用户回复“继续”“可以查看”或对工作流的泛泛肯定，不自动等于具体 Change Set 的批准。
9. Commit 之前校对聊天中展示的正文与待执行 Change Set `after` / Diff / SHA-256 **逐字一致**；若发现不一致，以机器记录为待审草案，重新展示正确的最终文件后再确认。旧展示或旧版本号不授予新版权限。

**显示规范模板：**

```text
Change Set: <ID> | SHA-256: <hash> | pending
Vault: <root> | N changes

01 | CREATE | wiki/concepts/finance/example.md
完整 Markdown（从 --- frontmatter 到最后一行）

02 | UPDATE | wiki/concepts/finance/existing.md
插入/替换/删除位置 + 精确 Diff（必要时展示修改后章节）

...直到全部 N 项展示完成...
Human Review: A 全部批准 / B 修改或排除指定项 / C 拒绝
```

**原则：看见什么，才批准什么；批准什么，才写入什么。**

## 审计与事务性

每次审批记录 Change Set ID、版本哈希、项目级状态、时间、精确页面补丁及对应来源。提交前验证目标页面 preimage、批准的补丁哈希与现存 Git 状态；不一致则暂停。审核通过不能证明用户已经掌握知识，也不能自动更新 Learning Hub。

## FIN3703A V1 迁移

旧 KD-01 至 KD-32 的长篇已批准正文及 A/B/C 历史不可删除、覆盖或冒充 V2 的最终 Vault 补丁。Knowledge Manager 可从中生成精简合并版 Change Set，由用户对最终精确补丁进行一次性迁移确认，而非逐条再次审查旧正文。

## 工程状态

本文是 V2 协议目标。当前 scripts/distiller.py 的 review() 仅支持 V1 单 Delta approve/reject，不代表 V2 Change Set 审批功能已实现；版本锁定、按项审批、Change Set Commit 及端到端验证完成前，不得声称自动入库已上线。
