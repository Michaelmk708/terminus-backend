import asyncio
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.core.database import engine, Base
from app.api import auth, heartbeat, ocr, webhooks, vault, dual_sign, identity
from app.services.watchdog import check_heartbeats
from app.core.database import engine, Base, get_db
# Create SQLite tables on startup
Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Terminus Oracle Online (SQLite Mode)")
    # Start the 30/14 day watchdog in the background
    watchdog = asyncio.create_task(check_heartbeats())
    yield
    watchdog.cancel()

app = FastAPI(title="Terminus Backend", lifespan=lifespan)

allowed_origins = [
    origin.strip()
    for origin in (
        os.getenv("CORS_ALLOWED_ORIGINS")
        or "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001,https://termnus.netlify.app"
    ).split(",")
    if origin.strip()
]

# THE BRIDGE: Enable CORS for local and production
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins, # This now includes port 3001 and Netlify
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register all routes
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(heartbeat.router, prefix="/api/heartbeat", tags=["Heartbeat"])
app.include_router(ocr.router, prefix="/api/ocr", tags=["OCR"])
app.include_router(webhooks.router, prefix="/api/webhooks", tags=["Webhooks"])
app.include_router(vault.router, prefix="/api/vault", tags=["Vault"])
app.include_router(dual_sign.router, prefix="/api/vault", tags=["Vault"])
app.include_router(identity.router, prefix="/api/identity", tags=["Identity"])

@app.get("/")
async def root():
    return {"status": "active", "db_mode": "sqlite"}