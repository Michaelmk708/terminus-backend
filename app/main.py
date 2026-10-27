"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Core & Infrastructure Config
from app.core.config import settings
from app.core.database import Base, engine
from app.core.migrations import run_startup_migrations
from app.core.middleware import ObservabilityMiddleware
from app.core.telemetry import setup_telemetry_logging

# Optional Handlers & Schema Overrides
try:
    from app.core.errors import register_error_handlers
except ImportError:
    register_error_handlers = None

try:
    from app.core.openapi import custom_openapi_schema
except ImportError:
    custom_openapi_schema = None

# Background Services & Async Queue Workers
from app.services.cron import scheduler
from app.services.event_processor import event_processor
from app.services.solana_listener import listen_to_solana_events
from app.services.watchdog import check_heartbeats

# API Routers
from app.api import (
    auth,
    beneficiary,
    claims,
    dual_sign,
    heartbeat,
    identity,
    observability,
    ocr,
    vault,
    webhooks,
)

logger = logging.getLogger("terminus.main")

# Initialize structured JSON logging
setup_telemetry_logging(log_level=settings.LOG_LEVEL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Application Lifespan Event Handler.
    # Manages startup execution (migrations, background queue workers) and graceful shutdown.
    logger.info("Starting Terminus Protocol Engine...")

    # 1. Run Alembic Database Migrations on Startup
    try:
        run_startup_migrations()
    except Exception as exc:
        logger.warning(f"Startup migration warning (falling back to ORM table creation): {exc}")
        Base.metadata.create_all(bind=engine)

    # 2. Start Background Cron Task Scheduler
    if settings.FEATURE_ENABLE_CRON_SCHEDULER:
        try:
            scheduler.start()
            logger.info("Background cron scheduler started.")
        except Exception as exc:
            logger.error(f"Failed to start cron scheduler: {exc}")

    # 3. Launch Async Queue Workers & Event Listeners
    event_worker_task = asyncio.create_task(event_processor.start_worker())
    watchdog_task = asyncio.create_task(check_heartbeats())
    
    solana_ws_task = None
    if settings.FEATURE_ENABLE_SOLANA_WS:
        solana_ws_task = asyncio.create_task(listen_to_solana_events())

    logger.info("🚀 Terminus Protocol Engine Online & Fully Operational.")

    yield  # Application accepts incoming HTTP requests during this window

    # --- Graceful Shutdown Sequence ---
    logger.info("Initiating graceful shutdown for Terminus Protocol Engine...")

    # Stop Webhook Event Processor Worker
    event_processor.stop_worker()
    event_worker_task.cancel()

    # Cancel Background Watchdog & Listener Tasks
    watchdog_task.cancel()
    if solana_ws_task:
        solana_ws_task.cancel()

    # Stop Cron Scheduler
    if settings.FEATURE_ENABLE_CRON_SCHEDULER:
        try:
            scheduler.stop()
        except Exception:
            pass

    # Await cleanup of background task cancellation
    await asyncio.gather(
        event_worker_task,
        watchdog_task,
        *([solana_ws_task] if solana_ws_task else []),
        return_exceptions=True
    )
    logger.info("Terminus Protocol Engine shutdown complete.")


# Initialize FastAPI Application Context
app = FastAPI(
    title=settings.PROJECT_NAME,
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Custom Exception Handlers & OpenAPI Schema Customizer
if register_error_handlers:
    register_error_handlers(app)

if custom_openapi_schema:
    app.openapi = lambda: custom_openapi_schema(app)

# Observability, Latency Tracing, and Prometheus Middleware
app.add_middleware(ObservabilityMiddleware)

# Cross-Origin Resource Sharing (CORS) Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Route Registrations
app.include_router(observability.router)  # Exposes /metrics, /health/readiness, /health/detailed
app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["Auth"])
app.include_router(heartbeat.router, prefix=f"{settings.API_V1_STR}/heartbeat", tags=["Heartbeat"])
app.include_router(ocr.router, prefix=f"{settings.API_V1_STR}/ocr", tags=["OCR"])
app.include_router(vault.router, prefix=f"{settings.API_V1_STR}/vault", tags=["Vault"])
app.include_router(claims.router, prefix=f"{settings.API_V1_STR}/claims", tags=["Claims"])
app.include_router(identity.router, prefix=f"{settings.API_V1_STR}/identity", tags=["Identity"])
app.include_router(webhooks.router, prefix=f"{settings.API_V1_STR}/webhooks", tags=["Webhooks"])
app.include_router(dual_sign.router, prefix=f"{settings.API_V1_STR}/dual-sign", tags=["Dual Sign"])
app.include_router(beneficiary.router, prefix=f"{settings.API_V1_STR}/beneficiary", tags=["Beneficiary"])


@app.get("/", tags=["System Information"])
async def root():
    return {
        "status": "active",
        "project": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "api_version": "v1",
        "docs_url": "/docs",
        "metrics_url": "/metrics",
        "health_url": "/health/detailed"
    }
"""

from fastapi import FastAPI, Form, File, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import time

app = FastAPI(title="Terminus API (Pitch Mode)")

# Allow Vercel frontend to communicate with Render backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- IN-MEMORY PITCH DATABASE ---
pitch_db = {
    "assets": [],
    "messages": [],
    "state": "ACTIVE",
    "streak": 42,
    "challenge_period_days": 90,
    "prompt_answered_today": False
}

class MessagePayload(BaseModel):
    owner_pubkey: str
    recipient: str
    title: str
    body: str
    lit_hash: str

# --- AUTHENTICATION BYPASS ---
@app.post("/api/v1/auth/login")
async def mock_login(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
        
    return {
        "status": "success",
        "user": {
            "email": data.get("email", "investor@demo.com"),
            "pubkey": "web3_pitch_mode_secured",
            "id": 1
        },
        "access_token": "mock_jwt_token_for_pitch"
    }

@app.post("/api/v1/auth/register")
async def mock_register(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
        
    return {
        "status": "success",
        "user": {
            "email": data.get("email", "investor@demo.com"),
            "pubkey": "web3_pitch_mode_secured",
            "id": 1
        },
        "access_token": "mock_jwt_token_for_pitch"
    }

# --- NON-TECHNICAL PITCH TIP ---
@app.get("/api/v1/public/random-tip")
async def random_tip():
    return {
        "tip": "Terminus ensures your family is taken care of, seamlessly passing on your digital assets and private letters when you no longer can."
    }

# --- DASHBOARD MOCKS ---
@app.get("/api/v1/vault/state/{pubkey}")
async def get_state(pubkey: str):
    return pitch_db

@app.get("/api/v1/vault/assets/{pubkey}")
async def get_assets(pubkey: str):
    return {"assets": pitch_db["assets"]}

@app.get("/api/v1/vault/messages/{pubkey}")
async def get_messages(pubkey: str):
    return {"messages": pitch_db["messages"]}

@app.post("/api/v1/vault/add-asset")
async def add_asset(
    owner_pubkey: str = Form(...),
    name: str = Form(...),
    category: str = Form(...),
    note: str = Form(""),
    value: str = Form("0"),
    file: UploadFile = File(None)
):
    new_asset = {
        "id": int(time.time()),
        "name": name,
        "category": category,
        "note": note,
        "value": float(value) if value else 0,
        "createdAt": int(time.time()),
        "hasFile": file is not None
    }
    pitch_db["assets"].insert(0, new_asset)
    return {"status": "success", "asset": new_asset}

@app.post("/api/v1/vault/add-message")
async def add_message(payload: MessagePayload):
    new_message = {
        "id": int(time.time()),
        "recipient": payload.recipient,
        "title": payload.title,
        "preview": "Securely locked until handover...",
        "createdAt": int(time.time())
    }
    pitch_db["messages"].insert(0, new_message)
    return {"status": "success", "message": new_message}

@app.post("/api/v1/claims/ping-heartbeat")
async def ping_heartbeat(
    vault_owner: str = Form(...), 
    journal_entry: str = Form(...)
):
    pitch_db["streak"] += 1
    pitch_db["prompt_answered_today"] = True
    return {"status": "success", "streak": pitch_db["streak"]}

@app.post("/api/v1/vault/defend")
async def defend_vault(
    vault_owner: str = Form(...),
    proof_of_life_sig: str = Form(...)
):
    pitch_db["state"] = "ACTIVE"
    return {"status": "defended"}

@app.get("/")
async def root():
    return {"status": "Terminus  is LIVE"}