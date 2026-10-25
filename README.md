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

Knowledge System Public Core 是从个人知识系统中提取的**可独立复现的本地核心版本**，不包含个人知识库或课程资料。

- **Academic Dashboard**：管理八个学科的任务、证据、经验值和等级；等级变更需要人工审核，不能视为自动认证。
- **Cognitive Engine**：提供学习会话记录、知识提炼、变更提案和审核工具。部分流程需要用户自行准备 Obsidian Vault。
- **本地启动**：`cd academic-dashboard && python3 app.py serve --port 8787`，然后访问 `http://127.0.0.1:8787`。
- **验证**：安装测试依赖后运行 `python3 scripts/verify_release.py`；v0.1 的 94 项离线测试全部通过。

这是 **v0.1 公开核心预发布版**，不是面向公网的多用户学习平台。不要把真实个人资料或有版权限制的课件提交到公开仓库。
