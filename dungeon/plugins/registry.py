"""A closed registry of trusted combat mechanisms.

Configuration names a registered kind; it cannot supply a module or callable.
Handlers receive deep-frozen JSON-like values and can return only schema-checked
state and approved combat operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Tuple
import copy

from jsonschema import Draft202012Validator

from dungeon.contracts.json_validation import validation_errors


class PluginError(ValueError):
    pass


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _validate(schema: Mapping[str, Any], value: Any, label: str) -> None:
    errors = validation_errors(schema, value)
    if errors:
        raise PluginError(f"{label}: {errors[0]}")


INT = {"type": "integer", "minimum": 0}
PARAM_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "minimum_spent": {"type": "integer", "minimum": 1, "maximum": 100},
        "shield_max_hp_bp": {"type": "integer", "minimum": 1, "maximum": 10000},
        "duration_ms": {"type": "integer", "minimum": 1, "maximum": 60000},
        "cooldown_ms": {"type": "integer", "minimum": 0, "maximum": 60000},
    },
    "required": ["minimum_spent", "shield_max_hp_bp", "duration_ms", "cooldown_ms"],
    "additionalProperties": False,
}
STATE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"cooldown_until_tick": INT,
                   "last_action_id": {"type": ["string", "null"], "minLength": 1}},
    "required": ["cooldown_until_tick", "last_action_id"],
    "additionalProperties": False,
}
EVENT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "type": {"const": "sword.momentum_spent"},
        "actor_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "action_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "actual_spent": INT,
        "origin": {"type": "string", "minLength": 1},
        "proc_depth": {"type": "integer", "minimum": 0, "maximum": 8},
    },
    "required": ["type", "actor_id", "action_id", "actual_spent", "origin", "proc_depth"],
    "additionalProperties": False,
}
CONTEXT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "tick": INT,
        "tick_rate": {"type": "integer", "minimum": 1, "maximum": 240},
        "max_hp": {"type": "integer", "minimum": 1},
    },
    "required": ["tick", "tick_rate", "max_hp"],
    "additionalProperties": False,
}
OPERATION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "operation": {"const": "shield.add"},
        "actor_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "amount": {"type": "integer", "minimum": 1, "maximum": 1000000},
        "duration_ticks": {"type": "integer", "minimum": 1, "maximum": 14400},
        "stacking": {"const": "refresh_max"},
    },
    "required": ["operation", "actor_id", "amount", "duration_ticks", "stacking"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class EffectResult:
    next_effect_state: Mapping[str, Any]
    operations: Tuple[Mapping[str, Any], ...]


Handler = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], EffectResult]


@dataclass(frozen=True)
class Mechanism:
    kind: str
    trigger: str
    handler: Handler
    params_schema: Mapping[str, Any]
    state_schema: Mapping[str, Any]
    event_schema: Mapping[str, Any]
    context_schema: Mapping[str, Any]
    operation_schema: Mapping[str, Any]
    max_operations: int


class Registry:
    def __init__(self) -> None:
        self._mechanisms: dict[str, Mechanism] = {}
        self._plugins: dict[str, str] = {}
        self._providers: dict[str, str] = {}

    def register(self, plugin_id: str, plugin_version: str, mechanism: Mechanism) -> None:
        existing_version = self._plugins.get(plugin_id)
        if mechanism.kind in self._mechanisms or (existing_version is not None and existing_version != plugin_version):
            raise PluginError("duplicate mechanism or incompatible plugin version")
        if mechanism.max_operations < 0:
            raise PluginError("invalid operation budget")
        for schema in (
            mechanism.params_schema, mechanism.state_schema, mechanism.event_schema,
            mechanism.context_schema, mechanism.operation_schema,
        ):
            Draft202012Validator.check_schema(schema)
        self._plugins[plugin_id] = plugin_version
        # Keep a private snapshot. Mutating a module-level schema or a returned
        # mechanism cannot change validation for a running ruleset.
        self._mechanisms[mechanism.kind] = copy.deepcopy(mechanism)
        self._providers[mechanism.kind] = plugin_id

    def require(self, plugin_id: str, plugin_version: str) -> None:
        if self._plugins.get(plugin_id) != plugin_version:
            raise PluginError(f"unavailable plugin {plugin_id}@{plugin_version}")

    def mechanism(self, kind: str) -> Mechanism:
        try:
            return copy.deepcopy(self._mechanisms[kind])
        except KeyError as exc:
            raise PluginError(f"unknown mechanism {kind}") from exc

    def plugin_versions(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self._plugins))

    def provider(self, kind: str) -> tuple[str, str]:
        self.mechanism(kind)
        plugin_id = self._providers[kind]
        return plugin_id, self._plugins[plugin_id]

    def invoke(
        self,
        kind: str,
        event: Mapping[str, Any],
        context: Mapping[str, Any],
        params: Mapping[str, Any],
        effect_state: Mapping[str, Any],
    ) -> EffectResult:
        mechanism = self.mechanism(kind)
        normalized = [_normalize(value) for value in (event, context, params, effect_state)]
        for schema, value, label in (
            (mechanism.event_schema, normalized[0], "event"),
            (mechanism.context_schema, normalized[1], "context"),
            (mechanism.params_schema, normalized[2], "params"),
            (mechanism.state_schema, normalized[3], "effect_state"),
        ):
            _validate(schema, value, label)
        if event["type"] != mechanism.trigger:
            raise PluginError("event is not subscribed by mechanism")
        # A deep copy severs aliases to mutable caller data. MappingProxyType and
        # tuples make accidental writes by trusted handlers fail immediately.
        result = mechanism.handler(*(_freeze(value) for value in normalized))
        if not isinstance(result, EffectResult):
            raise PluginError("handler returned an invalid result")
        if not isinstance(result.operations, (tuple, list)):
            raise PluginError("handler operations must be an ordered sequence")
        if len(result.operations) > mechanism.max_operations:
            raise PluginError("operation budget exceeded")
        _validate(mechanism.state_schema, _normalize(result.next_effect_state), "next_effect_state")
        for operation in result.operations:
            _validate(mechanism.operation_schema, _normalize(operation), "operation")
            if operation["actor_id"] != event["actor_id"]:
                raise PluginError("operation targets a different actor")
        return EffectResult(
            _freeze(_normalize(result.next_effect_state)),
            tuple(_freeze(_normalize(operation)) for operation in result.operations),
        )


def _shield_on_spend(event: Mapping[str, Any], context: Mapping[str, Any], params: Mapping[str, Any], state: Mapping[str, Any]) -> EffectResult:
    if event["proc_depth"] or event["origin"] != "main_weapon":
        return EffectResult(dict(state), ())
    if (event["actual_spent"] < params["minimum_spent"]
            or context["tick"] < state["cooldown_until_tick"]
            or event["action_id"] == state["last_action_id"]):
        return EffectResult(dict(state), ())
    tick_rate = context["tick_rate"]
    duration_ticks = (params["duration_ms"] * tick_rate + 999) // 1000
    cooldown_ticks = (params["cooldown_ms"] * tick_rate + 999) // 1000
    amount = max(1, (context["max_hp"] * params["shield_max_hp_bp"]) // 10000)
    return EffectResult(
        {"cooldown_until_tick": context["tick"] + cooldown_ticks,
         "last_action_id": event["action_id"]},
        ({"operation": "shield.add", "actor_id": event["actor_id"], "amount": amount,
          "duration_ticks": duration_ticks, "stacking": "refresh_max"},),
    )


def default_registry() -> Registry:
    registry = Registry()
    registry.register(
        "builtin.sword_rules", "0.1.0",
        Mechanism("sword.shield_on_spend", "sword.momentum_spent", _shield_on_spend,
                  PARAM_SCHEMA, STATE_SCHEMA, EVENT_SCHEMA, CONTEXT_SCHEMA,
                  OPERATION_SCHEMA, 1),
    )
    return registry
