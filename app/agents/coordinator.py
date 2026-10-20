"""AgentCoordinator: Orchestrates autonomous market rounds and AI-assisted participant trading."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ai.advisors import GridAIAgent, MarketAnalystAgent
from app.agents.ai.attribution import attribute_agent_outcome
from app.agents.ai.guard import AgentPolicyGuard
from app.agents.ai.memory import load_agent_memory
from app.agents.ai.provider import get_ai_provider
from app.agents.ai.schemas import GridAdvisorProposal, MarketExplanation
from app.agents.ai.strategies import HybridAgentRunner
from app.agents.base import Intent, MarketObservation
from app.agents.battery import BatteryAgent
from app.agents.ev import EVAgent
from app.agents.participants import (
    CommercialAgent,
    ConsumerAgent,
    GenerationView,
    GeneratorAgent,
    GridAgent,
    ProsumerAgent,
)
from app.audit import service as audit
from app.core.config import get_settings
from app.core.enums import (
    AgentAction,
    AssetStatus,
    AssetType,
    AuditEventType,
    CommitmentStatus,
    OrderSide,
    ParticipantType,
    StrategyMode,
)
from app.core.timeutil import ensure_utc, utcnow
from app.forecasting.reserve import ReservePolicy
from app.forecasting.service import demand_forecast, generation_forecast
from app.market.service import create_order, run_clearing
from app.models.ai import AIAgentConfiguration, AgentDecision, AgentMarketRound
from app.models.assets import EnergyAsset
from app.models.core import Microgrid, Participant
from app.models.ops import EnergyCommitment, Settlement
from app.risk.service import battery_state, ev_state, reserve_policy
from app.settlement.service import settle_commitment, verify_deliveries
from app.simulation import scenarios, signals
from app.simulation.engine import step
from app.simulation.state import SimConfig, get_config
from app.websocket.hub import queue_event

D = lambda x: Decimal(str(round(float(x), 6)))  # noqa: E731


async def _build_observation(session: AsyncSession, mg: Microgrid, ws: datetime, we: datetime, cfg: SimConfig) -> MarketObservation:
    mods = await scenarios.active_modifiers(session, mg.id, ws, we)
    tz = cfg.tz_offset_hours
    imp = D(signals.utility_price(ws, tz, mods.price_multiplier))
    exp = D(signals.export_price(ws, tz, mods.price_multiplier))
    horizon = [(D(signals.utility_price(ws + timedelta(hours=i), tz)) + D(signals.export_price(ws + timedelta(hours=i), tz))) / 2
               for i in range(24)]
    cur = (imp + exp) / 2
    return MarketObservation(
        ws, we, imp, exp, cur, max(horizon + [cur]), min(horizon + [cur]),
        mg.external_grid_connected, mg.operating_mode != "GRID_CONNECTED"
    )


class AgentCoordinator:
    """Production coordinator running complete autonomous market intervals."""

    def __init__(self):
        s = get_settings()
        self.provider = get_ai_provider()
        self.guard = AgentPolicyGuard()
        self.runner = HybridAgentRunner(self.provider, self.guard, timeout_seconds=s.ai_timeout_seconds)
        self.grid_advisor = GridAIAgent(self.provider)
        self.market_analyst = MarketAnalystAgent(self.provider)
        self.max_concurrency = s.ai_max_concurrency

    async def run_market_round(
        self,
        session: AsyncSession,
        microgrid_id: uuid.UUID,
        round_id: str | None = None,
        default_mode: StrategyMode | None = None,
        actor: str = "agent_coordinator",
    ) -> dict[str, Any]:
        """Executes a full closed-loop transactive market round with AI agents."""
        mg = await session.get(Microgrid, microgrid_id)
        if not mg:
            raise RuntimeError(f"Microgrid {microgrid_id} not found")

        cfg = await get_config(session, microgrid_id)
        now = cfg.sim_time
        round_id = round_id or f"round-{now.strftime('%Y%m%d%H%M')}-{uuid.uuid4().hex[:6]}"
        ws = now
        we = ws + timedelta(minutes=cfg.market_window_minutes)

        audit.set_correlation_id()
        queue_event(session, "MARKET_ROUND_STARTED", microgrid_id, {"round_id": round_id, "window_start": ws.isoformat()})
        await audit.record(session, AuditEventType.MARKET_ROUND_STARTED, "MarketRound", round_id, actor,
                           {"window_start": ws, "window_end": we}, microgrid_id)

        m = await _build_observation(session, mg, ws, we, cfg)
        hours = m.hours

        parts = list((await session.execute(select(Participant).where(
            Participant.microgrid_id == microgrid_id, Participant.active.is_(True)
        ).order_by(Participant.name))).scalars())
        assets = list((await session.execute(select(EnergyAsset).where(
            EnergyAsset.microgrid_id == microgrid_id
        ).order_by(EnergyAsset.name))).scalars())

        by_p: dict[uuid.UUID, list[EnergyAsset]] = defaultdict(list)
        for a in assets:
            by_p[a.participant_id].append(a)

        # Forecasts
        gen_fc: dict[uuid.UUID, Any] = {}
        dem_fc: dict[uuid.UUID, Any] = {}
        for a in assets:
            if a.generator and a.generator.renewable and a.status == AssetStatus.ONLINE.value:
                gen_fc[a.id] = await generation_forecast(session, a, ws, we, now, cfg)
            if a.load:
                dem_fc[a.id] = await demand_forecast(session, a, ws, we, now, cfg)

        tot_gen = sum(f.predicted_value for f in gen_fc.values())
        tot_dem = sum(f.predicted_value for f in dem_fc.values())
        surplus, deficit = max(0.0, tot_gen - tot_dem), max(0.0, tot_dem - tot_gen)

        # Configured strategy modes per agent
        s_mode = default_mode or StrategyMode(get_settings().default_agent_strategy_mode.upper())
        agent_configs = {
            c.agent_id: c for c in (await session.execute(select(AIAgentConfiguration).where(
                AIAgentConfiguration.microgrid_id == microgrid_id
            ))).scalars()
        }

        # Concurrently generate agent intents
        sem = asyncio.Semaphore(self.max_concurrency)
        intents_with_assets: list[tuple[Intent, EnergyAsset | None, AgentDecision | None]] = []

        async def _run_agent_task(coro):
            async with sem:
                return await coro

        tasks = []
        for p in parts:
            mine = by_p[p.id]
            pid = str(p.id)
            for a in mine:
                cfg_mode = StrategyMode(agent_configs.get(f"gen-{pid[:8]}", type("", (), {"strategy_mode": s_mode.value})).strategy_mode) if f"gen-{pid[:8]}" in agent_configs else s_mode

                if a.generator and a.generator.renewable:
                    f = gen_fc.get(a.id)
                    g = a.generator
                    pred = f.predicted_value if f else g.maximum_generation_kw * hours
                    low = f.lower_bound if f else pred
                    view = GenerationView(a.rated_power_kw, g.maximum_generation_kw, g.marginal_cost, g.renewable,
                                          pred, low, a.status == AssetStatus.ONLINE.value)
                    tasks.append((
                        a,
                        _run_agent_task(self.runner.decide_generator(pid, str(a.id), view, m, round_id, cfg_mode))
                    ))
                elif a.battery:
                    tasks.append((
                        a,
                        _run_agent_task(self.runner.decide_battery(pid, str(a.id), battery_state(a), m, round_id,
                                                                   surplus, deficit, cfg_mode))
                    ))
                elif a.ev:
                    # Deterministic fallback adapter for EV
                    ev_st = ev_state(a)
                    it = EVAgent().decide(pid, str(a.id), ev_st, m, deficit)
                    dec = AgentDecision(
                        microgrid_id=microgrid_id, agent_id=f"ev-{pid[:8]}", agent_type="EV",
                        mode="DETERMINISTIC", market_round_id=round_id, observation_hash=round_id,
                        prompt_version="ev_v1", provider="rules", model="none",
                        proposal=it.explanation(), confidence=1.0, reasoning_summary="EV rules",
                        guard_status="APPROVED", fallback_used=False, final_action=it.action.value,
                        final_quantity=float(it.quantity_kwh), latency_ms=0.0, created_at=utcnow(),
                    )
                    intents_with_assets.append((it, a, dec))
                elif a.load:
                    dem = dem_fc[a.id].predicted_value if a.id in dem_fc else a.load.base_demand_kw * hours
                    if a.load.load_kind == "COMMERCIAL":
                        frac = min(1.0, a.load.maximum_curtailment_kw / max(a.load.current_demand_kw, 1e-6)) if a.load.flexible else 0.0
                        for it in CommercialAgent().decide(pid, str(a.id), dem, frac, m):
                            dec = AgentDecision(
                                microgrid_id=microgrid_id, agent_id=f"comm-{pid[:8]}", agent_type="COMMERCIAL",
                                mode="DETERMINISTIC", market_round_id=round_id, observation_hash=round_id,
                                prompt_version="comm_v1", provider="rules", model="none",
                                proposal=it.explanation(), confidence=1.0, reasoning_summary="Commercial rules",
                                guard_status="APPROVED", fallback_used=False, final_action=it.action.value,
                                final_quantity=float(it.quantity_kwh), latency_ms=0.0, created_at=utcnow(),
                            )
                            intents_with_assets.append((it, a, dec))
                    else:
                        it = ConsumerAgent().decide(pid, str(a.id), dem, m, a.load.flexible, bool(p.preferences.get("renewable_only")))
                        dec = AgentDecision(
                            microgrid_id=microgrid_id, agent_id=f"cons-{pid[:8]}", agent_type="CONSUMER",
                            mode="DETERMINISTIC", market_round_id=round_id, observation_hash=round_id,
                            prompt_version="cons_v1", provider="rules", model="none",
                            proposal=it.explanation(), confidence=1.0, reasoning_summary="Consumer rules",
                            guard_status="APPROVED", fallback_used=False, final_action=it.action.value,
                            final_quantity=float(it.quantity_kwh), latency_ms=0.0, created_at=utcnow(),
                        )
                        intents_with_assets.append((it, a, dec))
                elif a.asset_type == AssetType.GRID_CONNECTION.value:
                    for it in GridAgent().decide(pid, str(a.id), mg.max_grid_import_kw, mg.max_grid_export_kw, m):
                        intents_with_assets.append((it, a, None))

        # Await concurrent AI agent decisions
        if tasks:
            results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
            for (asset, _), res in zip(tasks, results):
                if isinstance(res, Exception):
                    # Deterministic fallback on top-level failure
                    pid = str(asset.participant_id)
                    fallback_it = Intent("FallbackAgent", pid, str(asset.id), AgentAction.HOLD,
                                         reason_codes=["TOP_LEVEL_AGENT_ERROR"])
                    intents_with_assets.append((fallback_it, asset, None))
                else:
                    intent, decision = res
                    decision.microgrid_id = microgrid_id
                    session.add(decision)
                    intents_with_assets.append((intent, asset, decision))

        await session.flush()

        # Submit market orders
        created_orders = []
        for it, asset, dec in intents_with_assets:
            side = {AgentAction.BUY: OrderSide.BUY, AgentAction.CHARGE: OrderSide.BUY,
                    AgentAction.SELL: OrderSide.SELL, AgentAction.DISCHARGE: OrderSide.SELL,
                    AgentAction.V2G_SELL: OrderSide.SELL}.get(it.action)
            if side and it.quantity_kwh > 0 and it.limit_price and it.limit_price > 0 and asset is not None:
                order = await create_order(
                    session, uuid.UUID(it.participant_id), asset.id, side, it.quantity_kwh,
                    it.limit_price, ws, we, it.renewable_only, f"agent:{it.agent}", now
                )
                created_orders.append(order)

        # Run deterministic market clearing
        clearing = await run_clearing(session, microgrid_id, ws, we, actor, now)

        # Physical simulation step to collect meter data
        sim_step_result = await step(session, microgrid_id, actor)

        # Delivery verification
        verification = await verify_deliveries(session, microgrid_id, we, actor)

        # Settle active commitments
        settlement_results = []
        for c in verification.get("verified", []):
            try:
                st, _ = await settle_commitment(session, c.id, actor)
                settlement_results.append(st)
            except Exception:
                pass

        # Outcome attribution & memory updates
        outcomes = []
        lmp = float(clearing.clearing_price or m.expected_price)
        for it, asset, dec in intents_with_assets:
            if dec:
                agent_type = dec.agent_type
                executed_qty = float(dec.final_quantity)
                executed_act = dec.final_action
                out = await attribute_agent_outcome(
                    session, microgrid_id, round_id, dec.agent_id, agent_type, dec,
                    executed_action=executed_act, executed_quantity_kwh=executed_qty,
                    market_price=lmp, local_lmp=lmp, delivered_kwh=executed_qty,
                    baseline_counterfactual_reward=executed_qty * 0.05,
                )
                outcomes.append(out)

        # Advisory & Market Analyst explanation (with outage fallback)
        try:
            grid_proposal = await self.grid_advisor.advise(
                round_id, {"island_mode": m.island_mode, "max_feeder_loading_percent": 70.0}
            )
        except Exception:
            grid_proposal = GridAdvisorProposal(
                recommendation="NORMAL_OPERATION",
                reasoning_summary="AI Advisor offline or circuit open; deterministic controller in effect.",
                confidence=1.0,
            )

        try:
            explanation = await self.market_analyst.explain_round(
                round_id, {"expected_price": float(m.expected_price)},
                {"matched_kwh": float(clearing.matched_kwh), "trades_count": len(clearing.result.get("trades", []))}
            )
        except Exception:
            explanation = MarketExplanation(
                headline="Market Round Cleared Deterministically",
                summary="Market cleared using authoritative deterministic LP engine.",
                confidence=1.0,
            )

        # Record Market Round
        round_record = AgentMarketRound(
            microgrid_id=microgrid_id,
            round_id=round_id,
            snapshot_timestamp=ws,
            status="COMPLETED",
            participating_agents=[dec.agent_id for _, _, dec in intents_with_assets if dec],
            summary={
                "matched_kwh": float(clearing.matched_kwh),
                "clearing_price": float(clearing.clearing_price) if clearing.clearing_price else None,
                "orders_count": len(created_orders),
                "verified_commitments": len(verification.get("verified", [])),
                "settlements_count": len(settlement_results),
                "analyst_headline": explanation.headline,
            },
            created_at=utcnow(),
        )
        session.add(round_record)
        await session.flush()

        queue_event(session, "MARKET_ROUND_COMPLETED", microgrid_id, {
            "round_id": round_id,
            "matched_kwh": float(clearing.matched_kwh),
            "clearing_price": float(clearing.clearing_price) if clearing.clearing_price else None,
        })
        await audit.record(session, AuditEventType.MARKET_ROUND_COMPLETED, "MarketRound", round_id, actor,
                           {"matched_kwh": clearing.matched_kwh}, microgrid_id)

        return {
            "round_id": round_id,
            "window": [ws.isoformat(), we.isoformat()],
            "orders": created_orders,
            "clearing": clearing,
            "verification": verification,
            "settlements": settlement_results,
            "outcomes": outcomes,
            "grid_advisor": grid_proposal.model_dump(),
            "market_analyst": explanation.model_dump(),
        }
