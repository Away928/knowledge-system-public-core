# v0.1 public release scope

The original Knowledge System contains several private/local-first workspaces. This public distribution intentionally includes **only** two reusable components:

**Included:** Academic Dashboard source, local-only UI and unit tests; Cognitive Engine scripts, protocols, synthetic/testing materials, Python dependencies file, manifest and MIT license.

**Excluded:** private Obsidian Vault, personal Learning Hub, books, lectures, PDF/PPT documents, ChatGPT exports, accounts, local databases, authentication tokens and original development Git history.

## What has been tested?

- Academic Dashboard — 21 offline/localhost unit and HTTP tests.
- Cognitive Engine — 73 offline tests in the included subset.
- Release manifest integrity, Python source compilation and clean-archive tests.
- Continuous integration on GitHub Actions.

These checks do **not** prove that a real user's personal vault can be migrated end-to-end, that a model's generated judgment is correct, or that the local dashboard is safe to expose publicly.

## Working with your own data

Provide a new local content root as required by the chosen script. Never publish sensitive session logs or evidence. Human review is required before changing any claimed academic level or promoting knowledge changes into durable notes. The dashboard currently has no login system and should bind only to loopback.
