"""Container types for computed signals.

Signals are stored long (one row per signal per instant) rather than wide (one
column per signal). Adding a factor then costs no schema migration, and the
Parquet ``signal`` table stays stable while the research iterates. The cost is a
pivot on read, which Polars does cheaply.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["SignalRow", "SignalSet"]


@dataclass(frozen=True, slots=True)
class SignalRow:
    """One signal value at one instant.

    Attributes:
        book_seq: The book update count this was computed from. Ties the value
            back to an exact point in a recording, which wall clock cannot do
            across replays.
        value: ``float`` deliberately -- signals are research outputs, not
            money, and the Parquet layer stores them as ``float64``.
        levels: How many book levels fed the computation, when the factor is
            depth-parameterised. ``0`` for factors that only use the top.
    """

    symbol: str
    book_seq: int
    recv_ns: int
    recv_wall_ns: int
    name: str
    value: float
    levels: int = 0


@dataclass(slots=True)
class SignalSet:
    """All signals computed at one instant, ready to be written as rows."""

    symbol: str
    book_seq: int
    recv_ns: int
    recv_wall_ns: int
    values: dict[str, float] = field(default_factory=dict)
    levels: dict[str, int] = field(default_factory=dict)

    def add(self, name: str, value: float, *, levels: int = 0) -> None:
        self.values[name] = value
        if levels:
            self.levels[name] = levels

    def rows(self) -> list[SignalRow]:
        return [
            SignalRow(
                symbol=self.symbol,
                book_seq=self.book_seq,
                recv_ns=self.recv_ns,
                recv_wall_ns=self.recv_wall_ns,
                name=name,
                value=value,
                levels=self.levels.get(name, 0),
            )
            for name, value in self.values.items()
        ]
