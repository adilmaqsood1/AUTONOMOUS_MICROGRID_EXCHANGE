"""Grid feasibility: pure, replaceable network model.

`GridFeasibility` is the interface the market/risk/balancing code depends on. The MVP
implementation (`NetworkFlowFeasibility`) is a max-flow approximation on a capacitated
undirected graph with line flows tracked per direction. It is NOT a physical power-flow:
parallel paths are split to maximise transfer rather than by impedance. A DC/AC OPF
implementation can replace it by implementing the same interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field

EPS = 1e-9


@dataclass
class LineState:
    id: str
    a: str
    b: str
    capacity_kw: float
    flow_kw: float = 0.0  # signed, a->b positive
    active: bool = True


@dataclass
class TransferResult:
    requested_kw: float
    accepted_kw: float
    deltas: dict[str, float] = field(default_factory=dict)  # line_id -> signed delta
    binding_lines: list[str] = field(default_factory=list)

    @property
    def rejected_kw(self) -> float:
        return max(0.0, self.requested_kw - self.accepted_kw)


class GridFeasibility(ABC):
    @abstractmethod
    def available_transfer_kw(self, src: str, dst: str) -> float: ...

    @abstractmethod
    def request_transfer(self, src: str, dst: str, kw: float, commit: bool = True) -> TransferResult: ...


class NetworkFlowFeasibility(GridFeasibility):
    def __init__(self, lines: list[LineState]):
        self.lines: dict[str, LineState] = {l.id: l for l in sorted(lines, key=lambda x: x.id)}
        self._adj: dict[str, list[tuple[str, str, int]]] = {}
        for l in self.lines.values():
            self._adj.setdefault(l.a, []).append((l.id, l.b, +1))
            self._adj.setdefault(l.b, []).append((l.id, l.a, -1))
        for k in self._adj:
            self._adj[k].sort()

    def clone(self) -> "NetworkFlowFeasibility":
        return NetworkFlowFeasibility([LineState(l.id, l.a, l.b, l.capacity_kw, l.flow_kw, l.active)
                                       for l in self.lines.values()])

    def _residual(self, line_id: str, direction: int, deltas: dict[str, float]) -> float:
        l = self.lines[line_id]
        if not l.active:
            return 0.0
        f = l.flow_kw + deltas.get(line_id, 0.0)
        return max(0.0, l.capacity_kw - f) if direction == +1 else max(0.0, l.capacity_kw + f)

    def _max_flow(self, src: str, dst: str, limit: float) -> TransferResult:
        deltas: dict[str, float] = {}
        total = 0.0
        binding: set[str] = set()
        if src == dst:
            return TransferResult(limit, limit, {}, [])
        while total < limit - EPS:
            parent: dict[str, tuple[str, str, int]] = {}
            seen = {src}
            q = deque([src])
            while q and dst not in seen:
                n = q.popleft()
                for lid, nxt, d in self._adj.get(n, []):
                    if nxt in seen or self._residual(lid, d, deltas) <= EPS:
                        continue
                    seen.add(nxt)
                    parent[nxt] = (n, lid, d)
                    q.append(nxt)
            if dst not in parent:
                break
            path: list[tuple[str, int]] = []
            cur = dst
            while cur != src:
                p, lid, d = parent[cur]
                path.append((lid, d))
                cur = p
            bott = min(limit - total, min(self._residual(lid, d, deltas) for lid, d in path))
            for lid, d in path:
                deltas[lid] = deltas.get(lid, 0.0) + d * bott
                if self._residual(lid, d, deltas) <= EPS:
                    binding.add(lid)
            total += bott
        return TransferResult(limit, total, deltas, sorted(binding))

    def available_transfer_kw(self, src: str, dst: str) -> float:
        return self._max_flow(src, dst, 1e12).accepted_kw  # effectively unbounded probe

    def request_transfer(self, src: str, dst: str, kw: float, commit: bool = True) -> TransferResult:
        res = self._max_flow(src, dst, kw)
        res.requested_kw = kw
        if commit:
            for lid, dlt in res.deltas.items():
                self.lines[lid].flow_kw += dlt
        return res

    def flows(self) -> dict[str, float]:
        return {k: l.flow_kw for k, l in self.lines.items()}
