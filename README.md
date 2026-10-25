# Knowledge System — Public Core v0.1.0 RC1

Portable local-only tools extracted by allowlist from a **four-module private knowledge workspace**. Two functional submodules are included:

- **Academic Dashboard**: self-contained Python standard-library HTTP app for discipline levels, evidence-based assessments, quests and XP. Uses loopback only; no auth or multiuser permissions.
- **Cognitive Engine**: optional learning/session/capture/knowledge-change Python tools, protocol notes and offline unit tests; `PyYAML` is required. These tools were designed for an Obsidian vault and other local modules that **are not** bundled.

**Learning Hub and Obsidian Vault personal knowledge collections, raw course PDFs/slides, original sessions, chat exports, databases and private review data are intentionally excluded**. Reuse requires configuring your own content and verifying source rights.

## Run standalone Academic Dashboard

```bash
cd academic-dashboard
python3 app.py serve --port 8787
# Open http://127.0.0.1:8787 on the same computer
```

Only local 127.0.0.1 access is intended. The dashboard is a human-reviewed evidence tracker, not an automated skill certification authority.

## Test the distribution

```bash
python3 -m pip install -r requirements.txt "pytest>=8,<9"
python3 scripts/verify_release.py
```

The verifier checks file integrity, executes Academic Dashboard unit and HTTP tests, and runs the included Cognitive Engine unit suite. Install both PyYAML and pytest in the active Python environment before invoking it. Never expose its local API on the public Internet.

## Publication boundary

**License:** MIT for this distribution's original source and documentation. No user knowledge materials, third-party course content, model weights or external application code are included. Additional model/data integration tests are needed before any production claim. See `PUBLICATION_GATE.md`.
