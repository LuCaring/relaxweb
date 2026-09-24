"""Contract shared by the authoritative host and a deterministic combat simulator.

The host owns persistence, wallet operations and reward commits. A simulator sees
only frozen rules, ordered input, and deterministic services supplied by the host.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


JsonObject = Mapping[str, Any]


class SimulatorServices(Protocol):
    def random_int(self, stream: str, lower: int, upper: int) -> int:
        """Draw from a named, versioned stream; bounds are inclusive."""

    def emit(self, event: JsonObject) -> None:
        """Collect a combat event for this tick."""


@runtime_checkable
class Simulator(Protocol):
    def create(self, initial: JsonObject, rules: Any, services: SimulatorServices) -> Any:
        """Create state for a run pinned to ``rules``."""

    def step(
        self, state: Any, ordered_inputs: Sequence[JsonObject], services: SimulatorServices
    ) -> Any:
        """Advance exactly one authoritative tick and return the next state."""

    def snapshot(self, state: Any) -> JsonObject:
        """Return JSON-serializable state including RNG positions and plugin state."""

    def restore(self, snapshot: JsonObject, rules: Any, services: SimulatorServices) -> Any:
        """Restore a state under the same pinned rules and simulator version."""
