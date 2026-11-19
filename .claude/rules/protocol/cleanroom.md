# Clean room

The rules in `.claude/rules/protocol/` are the clean-room spec. Rules describe BDS 1.26.60.

## Roles

- RE agents read the IDA databases, bedrock-headers, the C++ reconstruction in `../NetherNet` and the notes in `.cache/re/`, and write what they find into `rules/protocol/` as `re.md` says.
- Implementers read only the rules, the code and public docs: RFCs, libwebrtc, aiortc, aioice, cryptography and Mojang's public guides. Never open `.cache/re/`, bedrock-headers, the IDA databases or `../NetherNet/src`, and never call the idalib MCP.
- An implementer who needs a fact the rules lack stops and asks for it.

## Version tags

- "(1.26.60)" marks a fact checked against BDS 1.26.60.
- "(1.26)" marks a fact observed on a 1.26 build before 1.26.60. Recheck it against 1.26.60.
- Tags mark BDS facts only. A library mapping or a choice of this library carries none.
- Change a tag only after checking the fact against that build.

## NDA boundary

The repository is public. Every committed rule stays inside this boundary.

- Allowed: wire field names, signaling message identifiers, channel labels, enum values with member names in the Python form `SessionError.X`, constants and defaults, byte-offset tables, algorithm steps as prose or numbered lists, state transitions, observed BDS behaviour, and public names: `server.properties` keys, HTTP routes from Mojang's public guide, and the libwebrtc, aiortc and aioice public API.
- Forbidden: addresses, mangled symbols, database paths in `rules/protocol/`, decompiled or C-shaped code, bedrock-headers text, internal BDS class, function and member names, and internal behaviour that never reaches the wire or the user.
- Name a component by its role, as "the LAN discovery base header" or "the HTTP signaling client", never by its BDS class.
