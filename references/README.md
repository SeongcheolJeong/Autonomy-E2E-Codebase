# Reference Assets Layout

This repository uses a repo-relative `references/` root for external documentation and repository snapshots.

## Expected Paths

- `references/applieddocs_v1.64/manual/v1.64/docs/<module>/index.md`
- `references/_reference_repos/<repo_name>/...`

## Notes

- Third-party sources are intentionally not vendored in this repository by default.
- Use `30_Projects/P_E2E_Stack/prototype/reference_repo_inventory.json` for pinned commits and source URLs.
- Place local snapshots under the paths above when running parity/migration workflows.
