"""AgentPolicyGuard: deterministic safety boundary validating and clamping AI proposals."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.agents.ai.context import AgentReasoningContext
from app.agents.ai.schemas import AgentStrategyProposal, GuardResult
from app.core.enums import AgentAction, GuardOutcome, LoadPriority


class AgentPolicyGuard:
    """Enforces physical asset limits, SOC boundaries, and safety invariants on AI proposals."""

    def __init__(self, min_confidence: float = 0.50):
        self.min_confidence = min_confidence

    def evaluate(
        self,
        proposal: AgentStrategyProposal,
        context: AgentReasoningContext,
    ) -> GuardResult:
        # 1. Confidence check
        if proposal.confidence < self.min_confidence:
            return GuardResult(
                status=GuardOutcome.REJECTED,
                approved_action=AgentAction.HOLD,
                reason_codes=["AI_CONFIDENCE_TOO_LOW"],
                original_proposal=proposal,
                details={"confidence": proposal.confidence, "min_required": self.min_confidence},
            )

        atype = context.agent_type.upper()
        ast = context.asset_state
        mst = context.market_state
        hours = float(mst.get("hours", 1.0))
        qty = proposal.quantity_kwh or Decimal("0")
        price = proposal.limit_price

        # 2. Generator Guard
        if "GENERATOR" in atype:
            rated_kw = float(ast.get("rated_power_kw", 0.0))
            max_kw = float(ast.get("maximum_generation_kw", rated_kw))
            physical_kwh = Decimal(str(round(max_kw * hours, 6)))
            mc = Decimal(str(ast.get("marginal_cost", "0.0")))

            if proposal.action not in (AgentAction.SELL, AgentAction.OFFER_RESERVE, AgentAction.HOLD, AgentAction.CURTAIL):
                return GuardResult(
                    status=GuardOutcome.REJECTED,
                    approved_action=AgentAction.HOLD,
                    reason_codes=["UNSUPPORTED_ACTION_FOR_GENERATOR"],
                    original_proposal=proposal,
                )

            if proposal.action == AgentAction.SELL:
                if qty > physical_kwh:
                    return GuardResult(
                        status=GuardOutcome.REJECTED,
                        approved_action=AgentAction.HOLD,
                        reason_codes=["AI_QUANTITY_EXCEEDS_SAFE_BOUND"],
                        original_proposal=proposal,
                        details={"requested_kwh": float(qty), "max_kwh": float(physical_kwh)},
                    )
                # Enforce price floor at marginal cost
                if price is not None and price < mc:
                    return GuardResult(
                        status=GuardOutcome.REJECTED,
                        approved_action=AgentAction.HOLD,
                        reason_codes=["PRICE_BELOW_MARGINAL_COST"],
                        original_proposal=proposal,
                    )
                return GuardResult(
                    status=GuardOutcome.APPROVED,
                    approved_action=AgentAction.SELL,
                    approved_quantity_kwh=qty,
                    approved_limit_price=price,
                    original_proposal=proposal,
                )

            return GuardResult(
                status=GuardOutcome.APPROVED,
                approved_action=proposal.action,
                approved_quantity_kwh=qty,
                approved_limit_price=price,
                approved_reserve_kw=proposal.reserve_kw,
                original_proposal=proposal,
            )

        # 3. Battery Guard
        if "BATTERY" in atype:
            cap = float(ast.get("capacity_kwh", 10.0))
            soc = float(ast.get("soc_percent", 50.0))
            min_soc = float(ast.get("minimum_soc_percent", 20.0))
            max_soc = float(ast.get("maximum_soc_percent", 95.0))
            rated_kw = float(ast.get("rated_power_kw", 5.0))
            max_energy_step = Decimal(str(round(rated_kw * hours, 6)))

            if proposal.action not in (
                AgentAction.CHARGE, AgentAction.DISCHARGE, AgentAction.SELL,
                AgentAction.STORE, AgentAction.OFFER_RESERVE, AgentAction.HOLD
            ):
                return GuardResult(
                    status=GuardOutcome.REJECTED,
                    approved_action=AgentAction.HOLD,
                    reason_codes=["UNSUPPORTED_ACTION_FOR_BATTERY"],
                    original_proposal=proposal,
                )

            # Max power/energy check
            if qty > max_energy_step:
                return GuardResult(
                    status=GuardOutcome.REJECTED,
                    approved_action=AgentAction.HOLD,
                    reason_codes=["AI_QUANTITY_EXCEEDS_SAFE_BOUND"],
                    original_proposal=proposal,
                    details={"requested_kwh": float(qty), "max_rate_kwh": float(max_energy_step)},
                )

            # Target SOC lower limit check
            if proposal.target_soc is not None and proposal.target_soc < min_soc:
                return GuardResult(
                    status=GuardOutcome.REJECTED,
                    approved_action=AgentAction.HOLD,
                    reason_codes=["AI_BELOW_MIN_SOC"],
                    original_proposal=proposal,
                    details={"target_soc": proposal.target_soc, "min_soc": min_soc},
                )

            # Discharge headroom check
            if proposal.action in (AgentAction.DISCHARGE, AgentAction.SELL):
                available_discharge_kwh = Decimal(str(round(max(0.0, (soc - min_soc) / 100.0 * cap), 6)))
                if qty > available_discharge_kwh:
                    return GuardResult(
                        status=GuardOutcome.REJECTED,
                        approved_action=AgentAction.HOLD,
                        reason_codes=["AI_BELOW_MIN_SOC"],
                        original_proposal=proposal,
                        details={"requested_kwh": float(qty), "available_discharge_kwh": float(available_discharge_kwh)},
                    )

            # Charge headroom check
            if proposal.action in (AgentAction.CHARGE, AgentAction.STORE):
                available_charge_kwh = Decimal(str(round(max(0.0, (max_soc - soc) / 100.0 * cap), 6)))
                if qty > available_charge_kwh:
                    return GuardResult(
                        status=GuardOutcome.REJECTED,
                        approved_action=AgentAction.HOLD,
                        reason_codes=["AI_EXCEEDS_MAX_SOC"],
                        original_proposal=proposal,
                        details={"requested_kwh": float(qty), "available_charge_kwh": float(available_charge_kwh)},
                    )

            return GuardResult(
                status=GuardOutcome.APPROVED,
                approved_action=proposal.action,
                approved_quantity_kwh=qty,
                approved_limit_price=price,
                approved_reserve_kw=proposal.reserve_kw,
                original_proposal=proposal,
            )

        # 4. EV Guard
        if "EV" in atype:
            cap = float(ast.get("capacity_kwh", 40.0))
            soc = float(ast.get("soc_percent", 50.0))
            req_soc = float(ast.get("required_departure_soc_percent", 80.0))
            hrs_left = float(ast.get("hours_until_departure", 2.0))
            charge_rate = float(ast.get("charging_power_kw", 7.0))
            discharge_rate = float(ast.get("discharging_power_kw", charge_rate))
            max_charge_kwh = Decimal(str(round(charge_rate * hours, 6)))

            # Hard invariant: V2G must never breach departure requirement
            if proposal.action == AgentAction.V2G_SELL:
                if soc < req_soc:
                    return GuardResult(
                        status=GuardOutcome.REJECTED,
                        approved_action=AgentAction.CHARGE,
                        reason_codes=["EV_DEPARTURE_REQUIREMENT_BREACHED"],
                        original_proposal=proposal,
                        details={"soc": soc, "required_soc": req_soc},
                    )
                headroom_kwh = Decimal(str(round((soc - req_soc) / 100.0 * cap, 6)))
                if qty > headroom_kwh:
                    return GuardResult(
                        status=GuardOutcome.REJECTED,
                        approved_action=AgentAction.HOLD,
                        reason_codes=["EV_DEPARTURE_REQUIREMENT_BREACHED"],
                        original_proposal=proposal,
                    )

            if proposal.action in (AgentAction.DEFER_CHARGING, AgentAction.HOLD):
                needed_kwh = max(0.0, (req_soc - soc) / 100.0 * cap)
                possible_kwh = charge_rate * hrs_left
                # If cannot defer without missing departure target, force CHARGE
                if needed_kwh >= possible_kwh * 0.95 and needed_kwh > 0:
                    return GuardResult(
                        status=GuardOutcome.REJECTED,
                        approved_action=AgentAction.CHARGE,
                        reason_codes=["EV_DEPARTURE_REQUIREMENT_BREACHED"],
                        original_proposal=proposal,
                    )

            if proposal.action == AgentAction.CHARGE and qty > max_charge_kwh:
                return GuardResult(
                    status=GuardOutcome.CLAMPED,
                    approved_action=AgentAction.CHARGE,
                    approved_quantity_kwh=max_charge_kwh,
                    approved_limit_price=price,
                    reason_codes=["QUANTITY_CLAMPED_TO_CHARGER_LIMIT"],
                    clamped=True,
                    original_proposal=proposal,
                )

            return GuardResult(
                status=GuardOutcome.APPROVED,
                approved_action=proposal.action,
                approved_quantity_kwh=qty,
                approved_limit_price=price,
                approved_reserve_kw=proposal.reserve_kw,
                original_proposal=proposal,
            )

        # 5. Consumer & Commercial Guard
        if "CONSUMER" in atype or "COMMERCIAL" in atype:
            priority = str(ast.get("priority", "NORMAL")).upper()
            flexible = bool(ast.get("flexible", False))

            # Critical load protection invariant
            if priority in (LoadPriority.CRITICAL.value, "CRITICAL"):
                if proposal.action in (AgentAction.DEFER_LOAD, AgentAction.ACCEPT_DEMAND_RESPONSE,
                                       AgentAction.CURTAIL, AgentAction.CURTAIL_FLEXIBLE_LOAD):
                    return GuardResult(
                        status=GuardOutcome.REJECTED,
                        approved_action=AgentAction.BUY,
                        reason_codes=["CRITICAL_LOAD_CANNOT_BE_CURTAILED"],
                        original_proposal=proposal,
                    )

            # Non-flexible load cannot be curtailed
            if not flexible and proposal.action in (AgentAction.DEFER_LOAD, AgentAction.CURTAIL,
                                                   AgentAction.CURTAIL_FLEXIBLE_LOAD,
                                                   AgentAction.ACCEPT_DEMAND_RESPONSE):
                return GuardResult(
                    status=GuardOutcome.REJECTED,
                    approved_action=AgentAction.BUY,
                    reason_codes=["NON_FLEXIBLE_LOAD_CANNOT_BE_CURTAILED"],
                    original_proposal=proposal,
                )

            return GuardResult(
                status=GuardOutcome.APPROVED,
                approved_action=proposal.action,
                approved_quantity_kwh=qty,
                approved_limit_price=price,
                original_proposal=proposal,
            )

        # Prosumer and others
        return GuardResult(
            status=GuardOutcome.APPROVED,
            approved_action=proposal.action,
            approved_quantity_kwh=qty,
            approved_limit_price=price,
            approved_reserve_kw=proposal.reserve_kw,
            original_proposal=proposal,
        )
