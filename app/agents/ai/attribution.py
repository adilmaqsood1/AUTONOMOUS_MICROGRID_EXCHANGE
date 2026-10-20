"""Outcome attribution service: calculates settled P&L, penalties, rewards, and counterfactuals."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ai.memory import DecisionEpisode, load_agent_memory, save_agent_memory
from app.agents.ai.rewards import (
    compute_battery_reward,
    compute_commercial_reward,
    compute_consumer_reward,
    compute_ev_reward,
    compute_generator_reward,
    compute_prosumer_reward,
)
from app.core.timeutil import utcnow
from app.models.ai import AgentDecision, AgentOutcome


async def attribute_agent_outcome(
    session: AsyncSession,
    microgrid_id: uuid.UUID,
    market_round_id: str,
    agent_id: str,
    agent_type: str,
    decision: AgentDecision | None,
    executed_action: str,
    executed_quantity_kwh: float,
    market_price: float,
    local_lmp: float,
    delivered_kwh: float,
    shortfall_kwh: float = 0.0,
    penalty_amount: float = 0.0,
    reserve_payment: float = 0.0,
    dr_payment: float = 0.0,
    degradation_cost: float = 0.0,
    baseline_counterfactual_reward: float | None = None,
) -> AgentOutcome:
    """Calculates realized economic outcome and records update in agent memory."""
    atype = agent_type.upper()
    proposed_act = decision.proposal.get("action", executed_action) if decision else executed_action
    proposed_qty = float(decision.proposal.get("quantity_kwh", executed_quantity_kwh)) if decision else executed_quantity_kwh

    delivery_ratio = (delivered_kwh / max(executed_quantity_kwh, 1e-6)) if executed_quantity_kwh > 0 else 1.0
    delivery_ratio = min(1.0, max(0.0, delivery_ratio))

    energy_bought = executed_quantity_kwh if executed_action in ("BUY", "CHARGE") else 0.0
    energy_sold = executed_quantity_kwh if executed_action in ("SELL", "DISCHARGE", "V2G_SELL") else 0.0
    energy_rev = energy_sold * market_price
    energy_cost = energy_bought * market_price

    if "GENERATOR" in atype:
        mc = float(decision.proposal.get("limit_price", 0.05)) if decision else 0.05
        components = compute_generator_reward(
            energy_revenue=energy_rev,
            generation_cost=delivered_kwh * mc,
            reserve_revenue=reserve_payment,
            penalties=penalty_amount,
        )
    elif "BATTERY" in atype:
        components = compute_battery_reward(
            arbitrage_revenue=energy_rev,
            charging_cost=energy_cost,
            reserve_revenue=reserve_payment,
            degradation_cost=degradation_cost,
            penalties=penalty_amount,
        )
    elif "EV" in atype:
        components = compute_ev_reward(
            v2g_revenue=energy_rev,
            charging_cost=energy_cost,
            reserve_revenue=reserve_payment,
            degradation_cost=degradation_cost,
            departure_penalty=penalty_amount,
        )
    elif "PROSUMER" in atype:
        components = compute_prosumer_reward(
            energy_sales=energy_rev,
            energy_purchases=energy_cost,
            reserve_revenue=reserve_payment,
            degradation_cost=degradation_cost,
            penalties=penalty_amount,
        )
    elif "COMMERCIAL" in atype:
        components = compute_commercial_reward(
            energy_cost=energy_cost,
            dr_revenue=dr_payment,
        )
    else:  # Consumer
        components = compute_consumer_reward(
            energy_cost=energy_cost,
            flexibility_compensation=dr_payment,
        )

    net_reward = components.net_reward

    outcome = AgentOutcome(
        microgrid_id=microgrid_id,
        agent_id=agent_id,
        decision_id=decision.id if decision else None,
        market_round_id=market_round_id,
        proposed_action=proposed_act,
        executed_action=executed_action,
        proposed_quantity=proposed_qty,
        executed_quantity=executed_quantity_kwh,
        market_price=market_price,
        local_lmp=local_lmp,
        energy_bought=energy_bought,
        energy_sold=energy_sold,
        energy_revenue=components.energy_revenue,
        energy_cost=components.energy_cost,
        reserve_revenue=components.reserve_revenue,
        demand_response_revenue=components.dr_revenue,
        battery_degradation_cost=components.degradation_cost,
        shortfall_penalty=components.shortfall_penalty,
        other_penalties=components.other_penalties,
        net_reward=net_reward,
        counterfactual_reward=baseline_counterfactual_reward,
        delivery_ratio=delivery_ratio,
        forecast_error=shortfall_kwh,
        guard_intervention=decision.guard_status != "APPROVED" if decision else False,
        fallback_used=decision.fallback_used if decision else False,
        created_at=utcnow(),
    )
    session.add(outcome)
    await session.flush()

    # Update operational memory
    memory = await load_agent_memory(session, microgrid_id, agent_id)
    episode = None
    if penalty_amount > 0 or abs(shortfall_kwh) > 2.0:
        episode = DecisionEpisode(
            market_round_id=market_round_id,
            timestamp=utcnow().isoformat(),
            scenario_or_event="Delivery Shortfall / Penalty Event",
            proposed_action=proposed_act,
            executed_action=executed_action,
            outcome_summary=f"Shortfall {shortfall_kwh:.2f} kWh incurred ${penalty_amount:.2f} penalty.",
            net_reward=net_reward,
            learnings="Reduce aggressive offer quantities when forecast variance is elevated.",
        )
    memory.record_outcome(
        net_reward=net_reward,
        delivery_ratio=delivery_ratio,
        forecast_error=shortfall_kwh,
        episode=episode,
    )
    await save_agent_memory(session, memory)

    return outcome
