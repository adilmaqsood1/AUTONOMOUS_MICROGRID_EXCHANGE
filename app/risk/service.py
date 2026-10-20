"""Stateful risk context for one market-clearing cycle.

Builds `RiskInput` for every candidate trade from the real asset/forecast/ledger/grid state,
runs the deterministic rule engine, and tracks consumption so later trades in the same cycle
cannot reuse capacity, collateral, grid headroom or reserve already granted.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.battery import BatteryState, deliverable_discharge_kwh
from app.agents.ev import EVState, max_v2g_kwh
from app.core.config import get_settings
from app.core.enums import GENERATOR_TYPES, AssetStatus, AssetType, CommitmentStatus, OperatingMode
from app.forecasting.reserve import ReservePolicy, tradable_capacity
from app.forecasting.service import generation_forecast
from app.grid.network import NetworkFlowFeasibility
from app.grid.service import OCCUPYING, load_network
from app.market.clearing import BookOrder, ValidationResult
from app.models.assets import EnergyAsset
from app.models.core import Microgrid, Participant
from app.models.ops import EnergyCommitment, RiskDecision
from app.risk.engine import RiskDecisionResult, RiskInput, evaluate_risk
from app.settlement.ledger import Ledger
from app.simulation.state import SimConfig

D = lambda x: Decimal(str(round(float(x), 6)))  # noqa: E731


def battery_state(a: EnergyAsset) -> BatteryState:
    b = a.battery
    return BatteryState(b.capacity_kwh, b.current_soc_percent, b.minimum_soc_percent, b.maximum_soc_percent,
                        b.maximum_charge_kw, b.maximum_discharge_kw, b.round_trip_efficiency,
                        b.degradation_cost_per_kwh, b.allocated_discharge_kwh, b.allocated_charge_kwh,
                        a.status == AssetStatus.ONLINE.value)


def ev_state(a: EnergyAsset) -> EVState:
    e = a.ev
    return EVState(e.battery_capacity_kwh, e.soc_percent, e.minimum_required_soc_percent,
                   e.minimum_reserve_soc_percent, e.departure_time, e.maximum_charge_rate_kw,
                   e.maximum_discharge_rate_kw, e.v2g_enabled,
                   e.connected and a.status == AssetStatus.ONLINE.value, e.allocated_discharge_kwh)


def reserve_policy() -> ReservePolicy:
    s = get_settings()
    return ReservePolicy(s.uncertainty_reserve_factor, s.operational_reserve_fraction)


async def seller_limits(session: AsyncSession, a: EnergyAsset, ws: datetime, we: datetime, now: datetime,
                        cfg: SimConfig, mg: Microgrid) -> dict:
    """Physical and forecast-risk-adjusted offer limits for an asset in a window (before commitments)."""
    hours = (we - ws).total_seconds() / 3600.0
    online = a.status == AssetStatus.ONLINE.value
    out = {"physical": Decimal("0"), "forecast": None, "battery": None, "ev": None, "grid": None}
    t = a.asset_type
    if t in {x.value for x in GENERATOR_TYPES} or (t == AssetType.SMALL_HYDRO.value):
        g = a.generator
        out["physical"] = D(g.maximum_generation_kw * hours) if online else Decimal("0")
        if online:
            if g.renewable:
                f = await generation_forecast(session, a, ws, we, now, cfg)
                out["forecast"] = tradable_capacity(f.predicted_value, f.lower_bound, reserve_policy()).tradable_kwh
            else:  # dispatchable: no weather uncertainty, only operational reserve
                out["forecast"] = tradable_capacity(g.maximum_generation_kw * hours,
                                                    g.maximum_generation_kw * hours, reserve_policy()).tradable_kwh
        else:
            out["forecast"] = Decimal("0")
    elif t == AssetType.BATTERY.value:
        bs = battery_state(a)
        reserve_soc = bs.min_soc_percent + 10.0
        avail = D(deliverable_discharge_kwh(bs, hours, reserve_soc))
        out["battery"] = avail
        out["physical"] = D(max(0.0, bs.max_discharge_kw * hours - bs.allocated_discharge_kwh)) if online else Decimal("0")
    elif t == AssetType.EV_BATTERY.value:
        es = ev_state(a)
        avail = D(max_v2g_kwh(es, ws, we))
        out["ev"] = avail
        out["physical"] = avail
    elif t == AssetType.GRID_CONNECTION.value:
        connected = mg.external_grid_connected and mg.operating_mode == OperatingMode.GRID_CONNECTED.value
        out["grid"] = D(mg.max_grid_import_kw * hours) if connected else Decimal("0")
        out["physical"] = out["grid"]
    return out


class MarketRiskContext:
    def __init__(self, session: AsyncSession, mg: Microgrid, ws: datetime, we: datetime, now: datetime,
                 cfg: SimConfig, network: NetworkFlowFeasibility):
        self.s, self.mg, self.ws, self.we, self.now, self.cfg, self.net = session, mg, ws, we, now, cfg, network
        self.hours = (we - ws).total_seconds() / 3600.0
        self.assets: dict[str, EnergyAsset] = {}
        self.participants: dict[uuid.UUID, Participant] = {}
        self.limits: dict[str, dict] = {}
        self.committed: dict[str, Decimal] = {}          # seller asset -> kWh already committed in window
        self.funds: dict[uuid.UUID, Decimal] = {}
        self.committed_total = Decimal("0")
        self.supply_total = Decimal("0")
        self.decisions: list[tuple[BookOrder, BookOrder, RiskDecisionResult]] = []
        self.settings = get_settings()

    async def load(self, orders: list[BookOrder]) -> "MarketRiskContext":
        ids = {uuid.UUID(o.asset_id) for o in orders}
        pids = {uuid.UUID(o.participant_id) for o in orders}
        rows = (await self.s.execute(select(EnergyAsset).where(EnergyAsset.id.in_(ids)))).scalars().all()
        self.assets = {str(a.id): a for a in rows}
        for p in (await self.s.execute(select(Participant).where(Participant.id.in_(pids)))).scalars():
            self.participants[p.id] = p
        # existing commitments overlapping the window consume seller capacity
        q = select(EnergyCommitment).where(EnergyCommitment.microgrid_id == self.mg.id,
                                           EnergyCommitment.status.in_(OCCUPYING),
                                           EnergyCommitment.delivery_start < self.we,
                                           EnergyCommitment.delivery_end > self.ws)
        for c in (await self.s.execute(q)).scalars():
            self.committed[str(c.seller_asset_id)] = self.committed.get(str(c.seller_asset_id), Decimal("0")) + c.quantity_kwh
            self.committed_total += c.quantity_kwh
        ledger = Ledger(self.s, self.mg.id)
        for pid in pids:
            self.funds[pid] = await ledger.available_funds(pid)
        # total dispatchable supply for the reserve-margin rule
        all_assets = (await self.s.execute(select(EnergyAsset).where(EnergyAsset.microgrid_id == self.mg.id))).scalars().all()
        total = Decimal("0")
        for a in all_assets:
            if a.status != AssetStatus.ONLINE.value:
                continue
            if a.generator:
                total += D(a.generator.maximum_generation_kw * self.hours)
            elif a.battery:
                total += D(deliverable_discharge_kwh(battery_state(a), self.hours, a.battery.minimum_soc_percent + 10))
            elif a.ev:
                total += D(max_v2g_kwh(ev_state(a), self.ws, self.we))
            elif a.asset_type == AssetType.GRID_CONNECTION.value and self.mg.external_grid_connected \
                    and self.mg.operating_mode == OperatingMode.GRID_CONNECTED.value:
                total += D(self.mg.max_grid_import_kw * self.hours)
        self.supply_total = total
        return self

    async def _limits(self, asset_id: str) -> dict:
        if asset_id not in self.limits:
            self.limits[asset_id] = await seller_limits(self.s, self.assets[asset_id], self.ws, self.we,
                                                         self.now, self.cfg, self.mg)
        return self.limits[asset_id]

    def build_input(self, buy: BookOrder, sell: BookOrder, qty: Decimal, lim: dict) -> RiskInput:
        price = ((buy.price + sell.price) / 2).quantize(Decimal("0.000001"))
        used = self.committed.get(sell.asset_id, Decimal("0"))
        net_kw = self.net.available_transfer_kw(sell.node_id, buy.node_id)
        grid_kwh = D(net_kw * self.hours)
        seller_p = self.participants[uuid.UUID(sell.participant_id)]
        buyer_funds = self.funds[uuid.UUID(buy.participant_id)]
        seller_funds = self.funds[uuid.UUID(sell.participant_id)]
        s = self.settings
        asset = self.assets[sell.asset_id]
        ri = RiskInput(
            requested_kwh=qty, price=price,
            seller_physical_capacity_kwh=lim["physical"] - used,
            seller_forecast_tradable_kwh=None if lim["forecast"] is None else lim["forecast"] - used,
            battery_available_kwh=None if lim["battery"] is None else lim["battery"] - used,
            ev_available_kwh=None if lim["ev"] is None else lim["ev"] - used,
            grid_available_kwh=grid_kwh,
            seller_reliability=seller_p.reliability_score, min_reliability=s.min_reliability_for_trading,
            buyer_collateral_available=buyer_funds,
            seller_collateral_available=seller_funds if sell.participant_id != buy.participant_id else None,
            collateral_fraction=s.collateral_fraction,
            grid_import_headroom_kwh=None if lim["grid"] is None else lim["grid"] - used,
            reserve_margin_headroom_kwh=self.supply_total * (Decimal("1") - Decimal(str(s.system_reserve_margin_fraction)))
            - self.committed_total,
        )
        return ri

    async def prepare(self, orders: list[BookOrder]) -> None:
        for o in orders:
            if o.side == "SELL":
                await self._limits(o.asset_id)

    def validator(self):
        """Synchronous validator for clear_market (limits are prefetched in `prepare`)."""
        def validate(buy: BookOrder, sell: BookOrder, qty: Decimal) -> ValidationResult:
            lim = self.limits[sell.asset_id]
            ri = self.build_input(buy, sell, qty, lim)
            res = evaluate_risk(ri)
            self.decisions.append((buy, sell, res))
            if res.approved_kwh > 0:
                # consume everything the approved quantity uses so later trades in this cycle see it
                self.net.request_transfer(sell.node_id, buy.node_id, float(res.approved_kwh) / self.hours, commit=True)
                self.committed[sell.asset_id] = self.committed.get(sell.asset_id, Decimal("0")) + res.approved_kwh
                self.committed_total += res.approved_kwh
                notional = ri.price * res.approved_kwh
                self.funds[uuid.UUID(buy.participant_id)] -= notional
                if sell.participant_id != buy.participant_id:
                    self.funds[uuid.UUID(sell.participant_id)] -= notional * self.settings.collateral_fraction
            code = res.reason_code if res.reason_code != "APPROVED" else "APPROVED"
            return ValidationResult(res.approved_kwh, code, res.message)
        return validate

    def decision_rows(self) -> list[RiskDecision]:
        rows = []
        for buy, sell, r in self.decisions:
            rows.append(RiskDecision(
                microgrid_id=self.mg.id, subject_type="TRADE_CANDIDATE", subject_id=f"{buy.id}:{sell.id}",
                approved=r.approved, reason_code=r.reason_code, message=r.message[:500],
                requested_kwh=r.requested_kwh, approved_kwh=r.approved_kwh, checks=r.as_dict()["checks"],
                created_at=self.now))
        return rows
