"""End-to-end REST API integration tests for all primary endpoint groups."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.enums import OrderSide
from app.main import app
from app.simulation.state import DEFAULT_START


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_health_check(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "app": "autonomous-microgrid-exchange", "version": "1.0.0"}


async def test_auth_login_and_me(client, demo):
    # 1. Login with seeded admin credentials
    res = await client.post("/auth/login", json={"email": "admin@greengrid.local", "password": "test-password-123"})
    assert res.status_code == 200
    token_data = res.json()
    assert "access_token" in token_data
    token = token_data["access_token"]

    # 2. Access /auth/me with bearer token
    me_res = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "admin@greengrid.local"
    assert me_res.json()["role"] == "ADMIN"


async def test_microgrid_and_assets_api(client, demo):
    # Login as operator
    login_res = await client.post("/auth/login", json={"email": "operator@greengrid.local", "password": "test-password-123"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Microgrids
    mg_res = await client.get("/microgrids", headers=headers)
    assert mg_res.status_code == 200
    mgs = mg_res.json()
    assert len(mgs) >= 1
    mg_id = mgs[0]["id"]

    # Assets
    assets_res = await client.get(f"/assets?microgrid_id={mg_id}", headers=headers)
    assert assets_res.status_code == 200
    assets = assets_res.json()
    assert len(assets) >= 30


async def test_simulation_control_and_scenarios(client, demo):
    login_res = await client.post("/auth/login", json={"email": "operator@greengrid.local", "password": "test-password-123"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Status
    status_res = await client.get("/simulation/status", headers=headers)
    assert status_res.status_code == 200
    data = status_res.json()
    assert "sim_time" in data
    assert "wind_speed_m_s" in data

    # Step
    step_res = await client.post("/simulation/step", json={"minutes": 15}, headers=headers)
    assert step_res.status_code == 200
    assert step_res.json()["steps_executed"] == 1

    # Preset injection: Preset B (Cloud Shock)
    preset_res = await client.post("/simulation/scenarios/presets/B", headers=headers)
    assert preset_res.status_code == 200
    ev = preset_res.json()
    assert ev["active"] is True
    sc_id = ev["id"]

    # List active scenarios
    sc_list = await client.get("/simulation/scenarios", headers=headers)
    assert sc_list.status_code == 200
    assert any(s["id"] == sc_id for s in sc_list.json())

    # Cancel scenario
    del_res = await client.delete(f"/simulation/scenarios/{sc_id}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["active"] is False


async def test_order_creation_and_orderbook(client, demo):
    # Login as prosumer1
    login_res = await client.post("/auth/login", json={"email": "prosumer1@greengrid.local", "password": "test-password-123"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    mg_id = demo["microgrid_id"]
    solar_id = demo["assets"]["Solar-1"]

    order_payload = {
        "asset_id": str(solar_id),
        "side": OrderSide.SELL.value,
        "quantity_kwh": "2.5",
        "limit_price": "0.08",
        "delivery_window_start": "2026-06-01T12:00:00Z",
        "delivery_window_end": "2026-06-01T13:00:00Z",
        "renewable_only": False
    }

    create_res = await client.post("/orders", json=order_payload, headers=headers)
    assert create_res.status_code == 200
    order_data = create_res.json()
    assert order_data["status"] == "OPEN"

    # Fetch orderbook
    book_res = await client.get(f"/orderbook?microgrid_id={mg_id}", headers=headers)
    assert book_res.status_code == 200
    book = book_res.json()
    assert len(book["sell_book"]) >= 1


async def test_analytics_and_audit(client, demo):
    login_res = await client.post("/auth/login", json={"email": "auditor@greengrid.local", "password": "test-password-123"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    mg_id = demo["microgrid_id"]

    # Analytics summary
    summary_res = await client.get(f"/analytics/summary/{mg_id}", headers=headers)
    assert summary_res.status_code == 200
    summary = summary_res.json()
    assert "total_generation_kwh" in summary
    assert "comparison" in summary

    # Audit events
    audit_res = await client.get(f"/audit/events?microgrid_id={mg_id}", headers=headers)
    assert audit_res.status_code == 200
    events = audit_res.json()
    assert len(events) >= 1
