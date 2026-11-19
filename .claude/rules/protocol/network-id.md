---
paths:
  - "src/nethernet/network_id.py"
  - "tests/test_network_id.py"
---

# NetworkID

- Forms, string forms and validity: SPEC §4.1
- Parsing a NetworkID string: SPEC §4.2
- NetworkID in JSON: SPEC §4.3
- Connection id: SPEC §4.4
- Correlation id: SPEC §4.5

## Connection id

- Draw it from 8 bytes of `secrets`.
