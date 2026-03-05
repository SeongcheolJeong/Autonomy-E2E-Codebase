# Contributing Guide

Thanks for contributing.

## Contribution Workflow

1. Open an issue first for non-trivial changes to align scope and acceptance criteria.
2. Fork the repository and create a feature branch.
3. Keep changes focused and small per PR.
4. Add or update tests for behavior changes.
5. Ensure validation commands pass before opening a PR.
6. Submit a PR with clear problem statement, change summary, and test evidence.

## Local Validation

From `30_Projects/P_E2E_Stack/prototype`:

```bash
make phase1-regression
make phase4-regression
make validate
```

For runtime-related changes, include at least one of:

```bash
make pipeline-runtime-available-contract-smoke
make pipeline-runtime-available-auto-docker-linux-fast-nobuild-dry-run
```

## Coding Standards

- Prefer explicit contracts and deterministic behavior.
- Keep code ASCII unless existing files require otherwise.
- Avoid broad refactors in the same PR as feature changes.
- Do not commit generated runtime artifacts (`runs`, `reports`, `batch_runs`, local DB/log files).

## Commit and PR Guidelines

- Use descriptive commit messages with scope.
- Include validation evidence in PR description (commands and outcomes).
- Note backward compatibility risks and migration impact when contracts change.
