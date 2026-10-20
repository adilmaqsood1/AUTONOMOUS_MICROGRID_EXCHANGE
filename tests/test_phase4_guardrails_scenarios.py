"""Phase 4 End-to-End Market Round, Scenario Adaptivity, Outage & API Tests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.agents.ai.evaluation import evaluate_decisions_and_outcomes
from app.agents.ai.experiments import ExperimentRunner
from app.agents.ai.provider import MockAIReasoningProvider, set_ai_provider
from app.agents.coordinator import AgentCoordinator
from app.core.enums import StrategyMode
from app.main import app
from app.models.ai import AgentDecision, AgentOutcome


@pytest.mark.asyncio
async def test_end_to_end_closed_loop_market_round(session, demo):
    mg_id = demo["microgrid_id"]
    coord = AgentCoordinator()

    # Run full 30-step autonomous market round in AI_ASSISTED mode
    result = await coord.run_market_round(
        session=session,
        microgrid_id=mg_id,
        round_id="test-e2e-round-1",
        default_mode=StrategyMode.AI_ASSISTED,
        actor="e2e_tester",
    )

    assert result["round_id"] == "test-e2e-round-1"
    assert "orders" in result
    assert "clearing" in result
    assert "verification" in result
    assert "settlements" in result
    assert "outcomes" in result
    assert "grid_advisor" in result
    assert "market_analyst" in result

    # Verify orders were matched and cleared
    clearing = result["clearing"]
    assert float(clearing.matched_kwh) > 0.0

    # Verify outcomes were attributed and net rewards recorded
    outcomes = result["outcomes"]
    assert len(outcomes) > 0
    assert any(o.net_reward != 0.0 for o in outcomes)

    # Verify Market Analyst explanation is grounded
    analyst = result["market_analyst"]
    assert analyst["headline"] != ""
    assert len(analyst["metric_references"]) > 0


@pytest.mark.asyncio
async def test_ai_outage_does_not_halt_market(session, demo):
    mg_id = demo["microgrid_id"]

    # Configure global AI provider to simulate complete outage
    outage_provider = MockAIReasoningProvider(force_failure=True)
    set_ai_provider(outage_provider)

    try:
        coord = AgentCoordinator()
        # Market must continue without throwing an exception
        result = await coord.run_market_round(
            session=session,
            microgrid_id=mg_id,
            round_id="test-outage-round",
            default_mode=StrategyMode.AI_ASSISTED,
            actor="outage_tester",
        )

        assert result["round_id"] == "test-outage-round"
        # Market clearing still succeeds via deterministic fallbacks
        clearing = result["clearing"]
        assert float(clearing.matched_kwh) > 0.0
    finally:
        set_ai_provider(None)  # Reset to standard mock provider


@pytest.mark.asyncio
async def test_late_ai_response_discards_and_falls_back(session, demo):
    mg_id = demo["microgrid_id"]

    # Simulate slow model with 5 second latency when timeout is 0.05s
    slow_provider = MockAIReasoningProvider(force_delay_seconds=0.20)
    set_ai_provider(slow_provider)

    try:
        coord = AgentCoordinator()
        coord.runner.timeout_seconds = 0.05  # Tight deadline
        result = await coord.run_market_round(
            session=session,
            microgrid_id=mg_id,
            round_id="test-late-deadline-round",
            default_mode=StrategyMode.AI_ASSISTED,
        )

        assert result["round_id"] == "test-late-deadline-round"
        assert float(result["clearing"].matched_kwh) > 0.0
    finally:
        set_ai_provider(None)


@pytest.mark.asyncio
async def test_experiment_runner_and_comparison(session, demo):
    mg_id = demo["microgrid_id"]
    runner = ExperimentRunner()

    exp = await runner.run_experiment(
        session=session,
        microgrid_id=mg_id,
        name="test_ai_exp",
        scenario="BASELINE",
        agent_mode="AI_ASSISTED",
        duration_steps=1,
        seed=42,
    )

    assert exp.name == "test_ai_exp"
    assert exp.results["duration_steps"] == 1
    assert exp.results["total_decisions"] > 0
    assert "total_net_reward" in exp.results


@pytest.mark.asyncio
async def test_agents_rest_api_endpoints(session, demo):
    mg_id = demo["microgrid_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Authenticate as operator
        login_res = await client.post("/auth/login", json={"email": "operator@greengrid.local", "password": "test-password-123"})
        assert login_res.status_code == 200
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. AI status
        r_status = await client.get("/agents/ai/status", headers=headers)
        assert r_status.status_code == 200
        data_status = r_status.json()
        assert data_status["configured"] is True
        assert "circuit_breaker_state" in data_status

        # 2. Leaderboard
        r_lead = await client.get(f"/agents/leaderboard?microgrid_id={mg_id}", headers=headers)
        assert r_lead.status_code == 200
        assert isinstance(r_lead.json(), list)

        # 3. Market rounds list
        r_rounds = await client.get(f"/agents/market-rounds?microgrid_id={mg_id}", headers=headers)
        assert r_rounds.status_code == 200
        assert isinstance(r_rounds.json(), list)
