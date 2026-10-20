"""Grid topology routers."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import math
from app.api.deps import SessionDep, operator, reader
from app.models.assets import EnergyAsset
from app.models.core import GridLine, GridNode, Microgrid
from app.schemas.schemas import GridLineResponse, GridNodeResponse

router = APIRouter(prefix="/grid", tags=["Grid Topology"])

CITY_COORDINATES = {
    "lahore": (31.5204, 74.3587),
    "san francisco": (37.7656, -122.4225),
    "london": (51.5074, -0.1278),
    "new york": (40.7128, -74.0060),
    "tokyo": (35.6762, 139.6503),
    "berlin": (52.5200, 13.4050),
    "singapore": (1.3521, 103.8198),
}

NODE_OFFSETS = {
    "SUBSTATION": (0.0, 0.0),
    "WIND_FARM": (0.0050, 0.0),
    "HYDRO": (0.0040, 0.0055),
    "GRID_TIE": (0.0035, -0.0055),
    "HOSPITAL": (0.0032, -0.0123),
    "STORAGE": (-0.0005, -0.0060),
    "RES_B": (-0.0045, 0.0045),
    "RES_A": (-0.0048, -0.0005),
    "COMMERCIAL": (-0.0042, -0.0050),
}


@router.get("/nodes", response_model=List[GridNodeResponse], dependencies=[Depends(reader)])
async def list_grid_nodes(microgrid_id: Optional[uuid.UUID] = None, session: AsyncSession = SessionDep):
    q = select(GridNode)
    if microgrid_id:
        q = q.where(GridNode.microgrid_id == microgrid_id)
    return (await session.execute(q.order_by(GridNode.name))).scalars().all()


@router.get("/lines", response_model=List[GridLineResponse], dependencies=[Depends(reader)])
async def list_grid_lines(microgrid_id: Optional[uuid.UUID] = None, session: AsyncSession = SessionDep):
    q = select(GridLine)
    if microgrid_id:
        q = q.where(GridLine.microgrid_id == microgrid_id)
    return (await session.execute(q.order_by(GridLine.id))).scalars().all()


@router.get("/topology/{microgrid_id}", dependencies=[Depends(reader)])
async def get_grid_topology(microgrid_id: uuid.UUID, session: AsyncSession = SessionDep):
    mg = await session.get(Microgrid, microgrid_id)
    location_label = mg.location_label if mg else ""
    label_lower = (location_label or "").lower()

    center_lat, center_lng = (31.5204, 74.3587) if "lahore" in label_lower else (37.7656, -122.4225)
    for city, coords in CITY_COORDINATES.items():
        if city in label_lower:
            center_lat, center_lng = coords
            break

    nodes_q = select(GridNode).where(GridNode.microgrid_id == microgrid_id).order_by(GridNode.name)
    lines_q = select(GridLine).where(GridLine.microgrid_id == microgrid_id).order_by(GridLine.id)
    assets_q = select(EnergyAsset).where(EnergyAsset.microgrid_id == microgrid_id)

    nodes = (await session.execute(nodes_q)).scalars().all()
    lines = (await session.execute(lines_q)).scalars().all()
    assets = (await session.execute(assets_q)).scalars().all()

    node_assets = {}
    for a in assets:
        nid = str(a.grid_node_id)
        if nid not in node_assets:
            node_assets[nid] = []
        node_assets[nid].append(a)

    total = max(len(nodes), 1)
    enriched_nodes = []
    for idx, n in enumerate(nodes):
        if n.name in NODE_OFFSETS:
            dlat, dlng = NODE_OFFSETS[n.name]
        else:
            angle = (idx / total) * 2 * math.pi - math.pi / 2
            dlat = 0.0045 * math.cos(angle)
            dlng = 0.0055 * math.sin(angle)

        attached = node_assets.get(str(n.id), [])
        total_rated = sum(float(a.rated_power_kw or 0.0) for a in attached)
        primary_type = attached[0].asset_type if attached else n.node_type

        # Authoritative color derived from backend node/asset type
        color = "#3B82F6"
        if n.node_type == "GENERATION" or "WIND" in n.name or "HYDRO" in n.name:
            color = "#06B6D4" if "WIND" in n.name else "#0EA5E9"
        elif n.node_type == "STORAGE" or "BATTERY" in primary_type or "STORAGE" in n.name:
            color = "#8B5CF6"
        elif n.node_type == "GRID_TIE" or "GRID" in n.name:
            color = "#10B981"
        elif "HOSPITAL" in n.name:
            color = "#EF4444"
        elif "COMMERCIAL" in n.name:
            color = "#F59E0B"
        elif "RES" in n.name:
            color = "#14B8A6" if "A" in n.name else "#2DD4BF"

        enriched_nodes.append({
            "id": str(n.id),
            "name": n.name,
            "node_type": n.node_type,
            "voltage_level": n.voltage_level,
            "voltage_magnitude_pu": 1.0,
            "voltage_angle_deg": 0.0,
            "latitude": round(center_lat + dlat, 5),
            "longitude": round(center_lng + dlng, 5),
            "rated_kw": round(total_rated, 1) if total_rated > 0 else (400.0 if "GRID" in n.name else 0.0),
            "connected_assets_count": len(attached),
            "color": color,
        })

    return {
        "location": {
            "label": location_label or "GreenGrid District",
            "latitude": center_lat,
            "longitude": center_lng,
        },
        "nodes": enriched_nodes,
        "lines": [
            {
                "id": str(l.id),
                "name": l.name,
                "from_node_id": str(l.from_node_id),
                "to_node_id": str(l.to_node_id),
                "capacity_kw": l.capacity_kw,
                "flow_kw": l.current_flow_kw,
                "loading_percent": round(abs(l.current_flow_kw) / max(l.capacity_kw, 1.0) * 100, 1),
                "congested": abs(l.current_flow_kw) >= l.capacity_kw * 0.95,
                "active": l.active,
            }
            for l in lines
        ],
    }


@router.patch("/lines/{line_id}/capacity", response_model=GridLineResponse, dependencies=[Depends(operator)])
async def update_line_capacity(line_id: uuid.UUID, capacity_kw: float, session: AsyncSession = SessionDep):
    line = await session.get(GridLine, line_id)
    if not line:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "grid line not found")
    line.capacity_kw = capacity_kw
    await session.commit()
    await session.refresh(line)
    return line


# --- Phase 2: Congestion Rents
from app.market import lmp as lmp_service
from app.schemas.schemas import CongestionRentsResponse


@router.get("/congestion-rents", response_model=CongestionRentsResponse, dependencies=[Depends(reader)])
async def get_grid_congestion_rents(microgrid_id: uuid.UUID, session: AsyncSession = SessionDep):
    res = await lmp_service.calculate_nodal_lmps(session, microgrid_id)
    return {
        "microgrid_id": res["microgrid_id"],
        "timestamp": res["timestamp"],
        "total_congestion_rent": res["total_congestion_rent"],
        "lines": res["lines"],
    }

