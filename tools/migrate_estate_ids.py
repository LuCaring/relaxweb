#!/usr/bin/env python3
"""Copy a SQLite save and rename catalog IDs using an external JSON mapping."""
import argparse
import json
from pathlib import Path
import sqlite3


def validate_mapping(mapping):
    groups = {"skins", "crops", "fish", "collectibles"}
    if not isinstance(mapping, dict) or set(mapping) - groups:
        raise ValueError("映射只支持 skins、crops、fish、collectibles")
    result = {}
    for group in groups:
        entries = mapping.get(group, {})
        if not isinstance(entries, dict) or any(
            not isinstance(old, str) or not old or not isinstance(new, str) or not new
            for old, new in entries.items()
        ):
            raise ValueError(f"{group} 必须是旧 ID 到新 ID 的字符串映射")
        entries = {old: new for old, new in entries.items() if old != new}
        if set(entries) & set(entries.values()):
            raise ValueError(f"{group} 不支持链式或循环映射")
        result[group] = entries
    return result


def migrate_ids(conn, mapping):
    """Caller owns the transaction. Touch catalog fields, never usernames or ledger text."""
    mapping = validate_mapping(mapping)
    items = {}
    for group, prefixes in (("crops", ("seed", "crop")), ("fish", ("fish",)),
                            ("collectibles", ("collectible", "treasure"))):
        for old, new in mapping[group].items():
            for prefix in prefixes:
                items[f"{prefix}:{old}"] = f"{prefix}:{new}"
    catches = {**mapping["fish"], **{
        f"treasure:{old}": f"treasure:{new}" for old, new in mapping["collectibles"].items()}}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def has_column(table, column):
        return table in tables and any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))

    def update(table, column, changes):
        if not has_column(table, column):
            return
        for old, new in changes.items():
            conn.execute(f'UPDATE {table} SET {column}=? WHERE {column}=?', (new, old))

    update("estate_profiles", "skin_id", mapping["skins"])
    update("estate_plots", "crop_id", mapping["crops"])
    update("estate_thefts", "crop_id", mapping["crops"])
    update("estate_fishing_sessions", "fish_id", catches)
    for table, column, changes in (
        ("estate_owned_skins", "skin_id", mapping["skins"]),
        ("estate_collections", "item_id", items),
        ("estate_inventory", "item_id", items),
    ):
        if not has_column(table, column):
            continue
        for old, new in changes.items():
            if table == "estate_inventory":
                conn.execute("INSERT INTO estate_inventory(username,item_id,quantity) "
                             "SELECT username,?,quantity FROM estate_inventory WHERE item_id=? "
                             "ON CONFLICT(username,item_id) DO UPDATE SET quantity=quantity+excluded.quantity",
                             (new, old))
            else:
                conn.execute(f"INSERT OR IGNORE INTO {table}(username,{column}) "
                             f"SELECT username,? FROM {table} WHERE {column}=?", (new, old))
            conn.execute(f"DELETE FROM {table} WHERE {column}=?", (old,))

    fields = {"skin_id": mapping["skins"], "crop_id": mapping["crops"],
              "fish_id": catches, "collectible_id": mapping["collectibles"]}

    def result_ids(value):
        if isinstance(value, list):
            return [result_ids(item) for item in value]
        if not isinstance(value, dict):
            return value
        output = {}
        for key, item in value.items():
            changes = fields.get(key, {})
            if key == "item_id":
                changes = mapping["crops"] if value.get("kind") == "seed" else items
            output[key] = changes.get(item, item) if isinstance(item, str) else result_ids(item)
        return output

    for table in ("estate_actions", "estate_fishing_sessions", "estate_mining_runs"):
        if not has_column(table, "result_json"):
            continue
        for rowid, raw in conn.execute(f"SELECT rowid,result_json FROM {table}").fetchall():
            if not raw:
                continue
            before = json.loads(raw)
            after = result_ids(before)
            if after != before:
                conn.execute(f"UPDATE {table} SET result_json=? WHERE rowid=?",
                             (json.dumps(after, ensure_ascii=False, separators=(",", ":")), rowid))


def migrate_copy(source, output, mapping):
    """Use SQLite backup for a consistent source snapshot; refuse to overwrite output."""
    mapping = validate_mapping(mapping)
    source, output = Path(source).resolve(), Path(output).resolve()
    with output.open("xb"):
        pass
    try:
        original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        try:
            target = sqlite3.connect(output)
            try:
                original.backup(target)
                with target:
                    migrate_ids(target, mapping)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("迁移后的数据库完整性检查失败")
            finally:
                target.close()
        finally:
            original.close()
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    args = parser.parse_args()
    migrate_copy(args.source, args.output, json.loads(args.mapping.read_text(encoding="utf-8")))
    print(f"已生成迁移副本：{args.output}；原数据库未修改。")


if __name__ == "__main__":
    main()
