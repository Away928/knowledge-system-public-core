# Knowledge System — Public Core

**Local-first tools for structured learning, evidence review, and knowledge workflows.**

[Release v0.1.0](https://github.com/Away928/knowledge-system-public-core/releases/tag/v0.1.1) · [MIT License](LICENSE) · [中文说明](#中文简介)

> **Scope:** This is a privacy-safe **subset of a larger personal Knowledge System**, not a hosted service, complete Obsidian vault, or automatic academic assessment product.

## What you can run

| Component | Included capabilities | Current limits |
| --- | --- | --- |
| [Academic Dashboard](academic-dashboard/README.md) | Local web dashboard for eight disciplines, assessments, learning tasks, evidence reviews, XP and levels | Human review only; no accounts, remote access, automatic grading, or institutional certification |
| [Cognitive Engine](cognitive-engine/scripts/) | Python tools for session capture, learning routing, trace distillation, proposed knowledge changes and review protocols | External vault and content are not bundled; some workflows require your own data and manual review |

These modules deliberately serve **different purposes**. The dashboard tracks explicitly reviewed outcomes; Cognitive Engine captures and prepares learning evidence. A study session or model-generated answer must **not** automatically raise a user's verified level.

## Quick start: Academic Dashboard

**Requirements:** Python 3.12 recommended and verified; no third-party packages for the dashboard itself.

```bash
git clone https://github.com/Away928/knowledge-system-public-core.git
cd knowledge-system-public-core/academic-dashboard
python3 app.py serve --port 8787
```

Open **http://127.0.0.1:8787** on the **same computer**. Use the dashboard to inspect the eight disciplines, create assessments or tasks, and review evidence. The app stores local state under `academic-dashboard/data/` (ignored by Git), so avoid using genuine personal evidence during evaluation unless you are comfortable storing it on your machine.

**Important:** The dashboard binds to the local loopback interface and **has no user authentication**. Do not expose its port to the Internet or a shared network. XP and levels are local human-review records, **not objective degrees or qualifications**.

## Test the public distribution

From the repository root:

```bash
python3 -m pip install -r requirements.txt "pytest>=8,<9"
python3 scripts/verify_release.py
```

The verification runs the included **Academic Dashboard (21 tests)** and **Cognitive Engine (73 tests)** suites, checks Python syntax, and validates the SHA-256 release manifest. In the v0.1 release, **94/94 offline tests passed** on Python 3.12; the same workflow also runs in [GitHub Actions](https://github.com/Away928/knowledge-system-public-core/actions).

The Cognitive Engine scripts can also be explored without connecting personal data:

```bash
python3 cognitive-engine/scripts/session_capture.py --help
python3 cognitive-engine/scripts/knowledge_manager.py --help
```

This does **not** demonstrate a complete real-world data ingestion/AI model workflow. Some commands are designed to integrate with an existing Obsidian vault and additional private Knowledge System modules.

## Data ownership and privacy

**Not in this repository:** personal Obsidian notes or vault, raw books/courses, PDFs, slides, chat exports, learning history, SQLite databases, credentials, or the author's private Learning Hub.

Your own records should remain outside version control. Do not commit real student work, personal assessments, copyrighted course materials, or provider tokens. Read [Security](SECURITY.md) and [Third-Party Notices](THIRD_PARTY_NOTICES.md).

## Development status

- **Available today:** standalone local dashboard; human-reviewed task/level/XP rules; generic Cognitive Engine source and tested offline modules.
- **Not claimed:** AI-generated exams or independent grading, verified academic proficiency, multi-user hosted deployment, synchronized Obsidian data, or live LLM end-to-end reliability.
- **Release policy:** small, reproducible steps with manual evidence review rather than automatic write-back to a personal knowledge base.

See [Release Scope](RELEASE_SCOPE.md) for the precise v0.1 boundary and [Contributing](CONTRIBUTING.md) for local development and issue reporting.

**License:** [MIT](LICENSE) for included original code and documentation. External libraries and applications retain their own licenses.

## 中文简介

**项目状态**：持续开发中。后续将持续完善功能、文档及实际应用案例。

Knowledge System 是一个面向自主学习和知识管理的本地工具集，将学习任务、学习证据和知识整理组织成可追踪、可复盘的流程。当前公开版本包含两个模块。

### 核心模块

1. **Academic Dashboard**：管理八个学科的学习任务、评估记录、证据以及经验值和等级；学习成果由使用者审核确认。
2. **Cognitive Engine**：记录学习会话，整理学习线索，提炼知识，并为知识库更新生成待审核的修改建议。

### 如何使用

- **体验学习面板**：克隆仓库后进入 `academic-dashboard`，运行 `python3 app.py serve --port 8787`，在本机浏览器打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)。
- **探索知识处理工具**：查看 `cognitive-engine/scripts/` 中的 Python 脚本；部分流程需要自行准备学习资料或 Obsidian 知识库。
- **详细说明**：参阅 [Academic Dashboard](academic-dashboard/README.md) 与上方英文使用指南。

公开版本不包含个人笔记、完整 Obsidian Vault 或私人学习数据。
