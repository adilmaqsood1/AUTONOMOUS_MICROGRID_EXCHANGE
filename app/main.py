"""FastAPI application entry point for the Autonomous Transactive Microgrid Energy Exchange."""
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import rate_limit_middleware
from app.api.routers.assets import battery_router, ev_router, router as assets_router
from app.api.routers.audit import analytics_router, audit_router
from app.api.routers.auth import router as auth_router
from app.api.routers.balancing import balancing_router, dr_router
from app.api.routers.forecasts import router as forecasts_router
from app.api.routers.grid import router as grid_router
from app.api.routers.blockchain import router as blockchain_router
from app.api.routers.market import router as market_router
from app.api.routers.meters import router as meters_router
from app.api.routers.microgrids import microgrid_router, participant_router
from app.api.routers.optimization import opt_router, risk_router
from app.api.routers.orders import router as orders_router
from app.api.routers.provenance import router as provenance_router
from app.api.routers.resilience import router as resilience_router
from app.api.routers.settlements import ledger_router, settlement_router
from app.api.routers.simulation import router as simulation_router
from app.api.routers.surveillance import router as surveillance_router
from app.api.routers.wallets import dev_funding_router, router as wallets_router
from app.api.routers.ws import router as ws_router
from app.api.routers.agents import router as agents_router
from app.core.config import get_settings
from app.core.errors import DomainError
from app.db.session import init_engine
from app.models.meters import ImmutableRecordError
from app.websocket.hub import hub

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mgx")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings.cache_clear()
    settings = get_settings()
    log.info(f"Starting Autonomous Transactive Microgrid Energy Exchange backend using {settings.database_url}...")
    init_engine(settings.database_url)
    await hub.start_redis()
    yield
    log.info("Shutting down microgrid exchange backend...")
    await hub.stop()


app = FastAPI(
    title="Autonomous Transactive Microgrid Energy Exchange",
    description=(
        "Production-quality backend for transactive energy trading, autonomous agents, "
        "double-auction market clearing, physical grid constraints, multi-period dispatch LP, "
        "immutable smart metering, double-entry ledger settlement, and blockchain provenance."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.middleware("http")(rate_limit_middleware)


# Domain exception handlers
@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code, **exc.details},
    )


@app.exception_handler(ImmutableRecordError)
async def immutable_record_error_handler(request: Request, exc: ImmutableRecordError):
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc), "code": "IMMUTABLE_RECORD"},
    )


# Health check
@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "ok",
        "app": "autonomous-microgrid-exchange",
        "version": "1.0.0",
    }


# Include all REST routers
app.include_router(auth_router)
app.include_router(microgrid_router)
app.include_router(participant_router)
app.include_router(assets_router)
app.include_router(battery_router)
app.include_router(ev_router)
app.include_router(grid_router)
app.include_router(meters_router)
app.include_router(forecasts_router)
app.include_router(orders_router)
app.include_router(market_router)
app.include_router(opt_router)
app.include_router(risk_router)
app.include_router(balancing_router)
app.include_router(dr_router)
app.include_router(settlement_router)
app.include_router(ledger_router)
app.include_router(provenance_router)
app.include_router(resilience_router)
app.include_router(audit_router)
app.include_router(analytics_router)
app.include_router(surveillance_router)
app.include_router(simulation_router)
app.include_router(ws_router)
app.include_router(blockchain_router)
app.include_router(wallets_router)
app.include_router(dev_funding_router)
app.include_router(agents_router)
