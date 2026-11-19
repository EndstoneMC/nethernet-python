# Comments

- Write no comments unless the user asks for one, in new code and in code you touch.
- Build and packaging files carry none: `pyproject.toml`, `.gitignore`, `.claude/settings.json` and hooks.
- Code cites nothing: no rule, guide, section or BDS reference.
- The `paths:` frontmatter of each rule is the trace from rule to module. Never repeat it in the code.
- Internal code has no docstrings.
- Only the public facade (`api.py`, `transport_api.py`, `__init__.py` and the names `nethernet` exports) keeps docstrings; it is a PyPI library.
- Those docstrings follow the Google style in `python.md`, and add sections only when they say something the signature does not.
- Leave existing comments alone unless your change makes them wrong.
- Put reasoning, evidence and format decoding in the commit message, never in the code.
- Never leave LLM notes: comments that explain why a change was made, reference the development process or a task step (`Task 8`), or restate what the code does.
- A requested comment is one short line, matching the density of the surrounding code.
- No parenthetical asides, `(e.g. ...)`, semicolon-joined clauses, or trailing comments that explain the line.
- Name concrete things in a comment (a peer, a signaling server), never abstract restatement.
- A `# noqa` names the code and a short reason: `# noqa: N818 - matches ConnectionFailed`.
- Delete a comment together with the dead code it describes, including the blank line above.
- README code samples carry no comments.
