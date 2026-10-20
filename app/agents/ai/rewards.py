"""Participant-specific economic reward functions for transparent outcome attribution."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RewardComponents:
    energy_revenue: float = 0.0
    energy_cost: float = 0.0
    reserve_revenue: float = 0.0
    dr_revenue: float = 0.0
    degradation_cost: float = 0.0
    shortfall_penalty: float = 0.0
    other_penalties: float = 0.0
    flexibility_cost: float = 0.0
    unmet_preference_penalty: float = 0.0

    @property
    def net_reward(self) -> float:
        return (
            (self.energy_revenue + self.reserve_revenue + self.dr_revenue)
            - (self.energy_cost + self.degradation_cost + self.shortfall_penalty
               + self.other_penalties + self.flexibility_cost + self.unmet_preference_penalty)
        )


def compute_generator_reward(
    energy_revenue: float,
    generation_cost: float,
    reserve_revenue: float = 0.0,
    penalties: float = 0.0,
) -> RewardComponents:
    """Generator: energy revenue + reserve revenue - generation cost - penalties."""
    return RewardComponents(
        energy_revenue=energy_revenue,
        energy_cost=generation_cost,
        reserve_revenue=reserve_revenue,
        shortfall_penalty=penalties,
    )


def compute_consumer_reward(
    energy_cost: float,
    flexibility_compensation: float = 0.0,
    unmet_preference_penalty: float = 0.0,
) -> RewardComponents:
    """Consumer: -(energy cost) + flexibility compensation - unmet preference penalty."""
    return RewardComponents(
        energy_cost=energy_cost,
        dr_revenue=flexibility_compensation,
        unmet_preference_penalty=unmet_preference_penalty,
    )


def compute_prosumer_reward(
    energy_sales: float,
    energy_purchases: float,
    reserve_revenue: float = 0.0,
    degradation_cost: float = 0.0,
    penalties: float = 0.0,
) -> RewardComponents:
    """Prosumer: energy sales + reserve revenue - energy purchases - degradation - penalties."""
    return RewardComponents(
        energy_revenue=energy_sales,
        energy_cost=energy_purchases,
        reserve_revenue=reserve_revenue,
        degradation_cost=degradation_cost,
        shortfall_penalty=penalties,
    )


def compute_battery_reward(
    arbitrage_revenue: float,
    charging_cost: float,
    reserve_revenue: float = 0.0,
    degradation_cost: float = 0.0,
    penalties: float = 0.0,
) -> RewardComponents:
    """Battery: arbitrage revenue + reserve revenue - charging cost - degradation - penalties."""
    return RewardComponents(
        energy_revenue=arbitrage_revenue,
        energy_cost=charging_cost,
        reserve_revenue=reserve_revenue,
        degradation_cost=degradation_cost,
        shortfall_penalty=penalties,
    )


def compute_ev_reward(
    v2g_revenue: float,
    charging_cost: float,
    reserve_revenue: float = 0.0,
    degradation_cost: float = 0.0,
    departure_penalty: float = 0.0,
) -> RewardComponents:
    """EV: V2G revenue + reserve revenue - charging cost - degradation - departure penalty."""
    return RewardComponents(
        energy_revenue=v2g_revenue,
        energy_cost=charging_cost,
        reserve_revenue=reserve_revenue,
        degradation_cost=degradation_cost,
        other_penalties=departure_penalty,
    )


def compute_commercial_reward(
    energy_cost: float,
    dr_revenue: float = 0.0,
    flexibility_cost: float = 0.0,
) -> RewardComponents:
    """Commercial: DR revenue - energy cost - operational flexibility cost."""
    return RewardComponents(
        energy_cost=energy_cost,
        dr_revenue=dr_revenue,
        flexibility_cost=flexibility_cost,
    )
