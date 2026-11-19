# GitHub

## Commits

- Commit, push, branch or tag only when the user asked in this conversation.
- Write subjects as `type(scope): imperative summary`, lower case, no trailing period.
- Use the types `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `style` and `revert`.
- Scope by the module or package touched: `transport`, `signaling`, `http`, `discovery`, `session`, `api`, `identity`, `framing`, `network-id`; `rules` for a change to `.claude/rules/` alone. Omit the scope for repo-wide changes.
- Write a prose body for every non-trivial fix: what was wrong, as a user of the library saw it, and what changed.
- Name in the body every rule file the commit changes, as `rules/protocol/framing.md`, and the fact it changed.
- Name the BDS build a behaviour was observed on, as `BDS 1.26.60`.
- Keep a commit to one change; a refactor and a behaviour change are two commits.
- Never add a `Co-Authored-By` line or any other attribution trailer.
- Never copy bedrock-headers content or `.cache/re/` notes into a commit, an issue or a pull request; they are under NDA (`protocol/cleanroom.md`).

## Branches

- `main` is the only branch. Never rewrite pushed history.
- endgate's `tests/pyproject.toml` pins `nethernet[http]` from `main` on GitHub, so a push changes its next integration run. The push that lands the rename to `nethernet-python` and the removal of the `http` extra must update endgate in the same change.
- endstone's `tests/plugin` runner imports this package for its NetherNet bots and LAN ping.
