"""
RecoveryAI — FastAPI Application Entry Point
"""

import logging

import asyncio
from contextlib import asynccontextmanager

import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.migrations import run_migrations
from app.routes import router
from app.scheduler import run_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run migrations (ensures tables & ALTER TABLE for columns)
    try:
        await run_migrations()
    except Exception as e:
        logging.getLogger(__name__).error("Migration on startup failed: %s", e)

    # Launch background autonomous scheduler task
    scheduler_task = asyncio.create_task(run_scheduler())
    yield
    # Cleanup
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass

# ── Application ───────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Autonomous, bounded AI revenue recovery system "
        "for Indian B2B and consumer payment workflows."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Process Time Profiling Middleware ─────────────────────────────────────────
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    process_time = time.perf_counter() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.4f}s"
    return response


# ── CORS (allow Next.js dev server) ──────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["Meta"], summary="Health check")
async def health() -> dict:
    """Liveness probe — returns service status."""
    return {"status": "healthy", "service": "RecoveryAI"}


app.include_router(router, prefix="/api", tags=["Recovery API"])
