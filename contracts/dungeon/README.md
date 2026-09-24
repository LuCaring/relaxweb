# Dungeon Beta F0 contracts

`python -m dungeon.tools validate-content content/dungeon/release.json` validates
the release and prints its pinned version and SHA-256 digest. A nonzero exit code
means the release cannot open new runs. The loader checks JSON Schema 2020-12,
strict integer fields, cross references, plugin providers and paths before it
constructs a `Ruleset`.

Python callers use `dungeon.content.load_ruleset(...)`. `content(name)` returns a
read-only snapshot; `mutable_content(name)` returns a detached JSON copy for a
policy constructor such as `FixedLevelPolicy`. `public_catalog()` is the only
current client-facing projection and excludes loot tables and effect parameters.
The run host should retain `reference()` and the matching in-memory ruleset for
the whole run. A digest alone cannot restore old plugin code.

`dungeon.contracts.simulator.Simulator` is an importable Protocol for the B-line
action simulator. This package does not contain an action simulator or the
run-time WebSocket commands described in the V1 draft. The fixtures exercise
the pure shield mechanism, a real growth policy quote, and the frozen
permanent-operation lifecycles below; a complete run fixture set arrives with
the R4 host.

The first trusted mechanism is `sword.shield_on_spend`. Its state contains
`cooldown_until_tick` and `last_action_id`; both must be saved in the simulator
snapshot. `Registry.invoke` checks the event, read-only context, parameters,
state, output operation and operation budget. Configuration only names reviewed
mechanisms; it cannot select an import path or execute a script.

## Beta permanent-operation messages (protocol_version 1)

`schemas/beta_messages.schema.json` freezes the eight `dungeon_beta_*` requests
registered by `server/dungeon/beta_protocol.py` (catalog, upgrade quote/commit,
directed offers, receipt lookup), the shared `dungeon_beta_result` /
`dungeon_beta_error` envelopes and the `dungeon_beta_invalidate` push. Run-time
messages (start/input/frame/sync) are deliberately absent until the R4 host.
`fixtures/upgrade_lifecycle.json` and `fixtures/trade_lifecycle.json` carry
happy-path and failure samples whose figures match the beta-core pack; the
protocol tests validate every fixture entry against this schema, so B-line fake
services and C-line samples stay byte-compatible with the server. To bootstrap
a test database with two real login accounts
(`beta_seller` with a tradable sword and boss progress, `beta_buyer` with
coins), run `python -m dungeon.tools init-test-db PATH --coins 1000`.
