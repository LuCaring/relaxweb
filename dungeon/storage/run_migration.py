"""Schema for the internal Beta run host (migration 4).

Imported by beta_schema's numbered migration runner. Existing migration scripts
must remain byte-for-byte unchanged because their checksums are persisted.
"""

RUN_MIGRATION = (4, """
CREATE TABLE dungeon_beta_runs (
    run_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    route_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ready','running','paused','finished','abandoned')),
    room_index INTEGER NOT NULL DEFAULT 0 CHECK(room_index >= 0),
    server_tick INTEGER NOT NULL DEFAULT 0 CHECK(server_tick >= 0),
    durable_tick INTEGER NOT NULL DEFAULT 0 CHECK(durable_tick >= 0),
    run_revision INTEGER NOT NULL DEFAULT 1 CHECK(run_revision > 0),
    control_epoch INTEGER NOT NULL DEFAULT 0 CHECK(control_epoch >= 0),
    last_input_seq INTEGER NOT NULL DEFAULT 0 CHECK(last_input_seq >= 0),
    ruleset_id TEXT NOT NULL,
    ruleset_hash TEXT NOT NULL,
    simulation_version INTEGER NOT NULL,
    save_version INTEGER NOT NULL,
    initial_json TEXT NOT NULL,
    checkpoint_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX dungeon_beta_one_active_run ON dungeon_beta_runs(user_id)
    WHERE status IN ('ready','running','paused');
CREATE INDEX dungeon_beta_runs_user ON dungeon_beta_runs(user_id,created_at);
CREATE TABLE dungeon_beta_room_rewards (
    run_id TEXT NOT NULL,
    room_index INTEGER NOT NULL CHECK(room_index >= 0),
    reward_kind TEXT NOT NULL CHECK(reward_kind = 'room'),
    result_json TEXT NOT NULL,
    committed_at INTEGER NOT NULL,
    PRIMARY KEY(run_id,room_index,reward_kind)
);
CREATE TRIGGER delete_user_dungeon_beta_runs AFTER DELETE ON users BEGIN
    DELETE FROM dungeon_beta_room_rewards WHERE run_id IN
        (SELECT run_id FROM dungeon_beta_runs WHERE user_id=OLD.id);
    DELETE FROM dungeon_beta_runs WHERE user_id=OLD.id;
END;
""")
