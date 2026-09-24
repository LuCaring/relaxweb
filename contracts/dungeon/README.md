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
action simulator. This F0 package does not contain an action simulator or the
WebSocket command handlers described in the V1 draft. The two fixtures here
exercise one pure shield mechanism and one real growth policy quote from the
pack. They are not a complete run/transaction fixture set.

The first trusted mechanism is `sword.shield_on_spend`. Its state contains
`cooldown_until_tick` and `last_action_id`; both must be saved in the simulator
snapshot. `Registry.invoke` checks the event, read-only context, parameters,
state, output operation and operation budget. Configuration only names reviewed
mechanisms; it cannot select an import path or execute a script.
