---
paths:
  - ".claude/rules/protocol/**"
  - ".cache/re/**"
---

# Reverse engineering

For RE agents only. Implementers never read this side (`protocol/cleanroom.md`).

## Notes to rules

- Raw findings go to `.cache/re/`. It is gitignored and under NDA: never commit, quote or move it.
- Turn a finding into a rule in the topic file whose `paths:` covers the module that implements it, one fact in one place.
- Write the rule inside the NDA boundary (`protocol/cleanroom.md`): the observable behaviour in prose, steps or an offset table, never the code or the names it came from.
- Tag it "(1.26.60)" once checked against 1.26.60. A fact the rules already hold gets its tag upgraded, not a second copy.
- The C++ reconstruction in `../NetherNet` is from 1.26.20. Use it as a guide only and confirm every fact against 1.26.60.

## IDA routing

- Client-only code, such as the client negotiator, the join flow, the HTTP signaling client and the client identity generator: the Windows client 1.26.60.5 database `D:\Projects\Endstone\bedrock-symbols\gamecore_x64_desktop\1.26.60.5\Minecraft.Windows.exe.i64`, with `Minecraft.Windows.pdb`.
- Shared NetherNet code and the server config: the Linux server 1.26.60 database `D:\Projects\Endstone\bedrock-symbols\linux_server_x64\1.26.60\bedrock_server.i64`, with names from `bedrock_server.debug`. Fall back to the client when its pre-flight finds no NetherNet names.
- Each database has one shared open copy under `scratchpad\ida\`, opened by the orchestrator. Never copy a database per agent.
- Agents attach to the shared session through the idalib MCP with `database:`. Never open, close or delete a session.
- The idalib server runs at most 4 workers. Never raise the cap.

## Method

- Pre-flight: before a batch, count the function names in the `NetherNet` namespace. Zero means the wrong database or missing symbols.
- Lambda symbols embed their source file, line and column. Use them to place each lambda.
- Recover leaf functions first, then virtuals, which always have a standalone body. Recover an inlined helper from its copies inside callers and reconcile them.
- Look for LAN id filters in the handler that owns each LAN event, not in the packet decoder. The decoder updates the peer table for every sender before any id filter runs.
- Read the signaling receive path on the Linux server, which splits it into one visitor function per message variant, rather than the one huge Windows body.
- The IDA instruction query's operand filters do not match memory displacements. Search for the instruction bytes instead.
- Re-derive the names of small IP helpers from their bodies before reading a filter predicate. Inherited names have been found inverted.
- With one fixed WebRTC UDP port every session shares one socket, so expect STUN 401 errors on the later sessions.
- Never guess. Mark a fact you could not confirm "(unverified)" and say what is missing.
- Never edit the bedrock-headers checkout.
