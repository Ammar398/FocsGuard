"""FastAPI application (spec sections 36, 51).

    uvicorn focusguard_backend.main:app --reload
    python -m focusguard_backend.main

WHY FASTAPI
-----------
Three reasons that actually matter here rather than being general praise:

* the Pydantic models in `api/v1/schemas.py` *are* the validation, so there is no
  second place where the wire contract is described and could drift;
* async support, which the live-score WebSocket in Phase 14 needs;
* automatic OpenAPI docs at `/docs`, which is a genuinely useful artefact to
  demonstrate at defence.

There was no reason to fall back to Flask.

VERSIONED FROM THE START
------------------------
Everything sits under `/api/v1`. Agents are installed software that updates on
their own schedule, so a deployed backend will always be serving some older
version - the prefix is what makes that survivable.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from focusguard_backend.api.v1 import agent, sessions
from focusguard_backend.api.v1.schemas import HealthResponse
from focusguard_backend.database.session import create_all, database_url, get_engine
from focusguard_shared.utilities import configure_logging, get_logger

logger = get_logger("backend.main")

API_PREFIX = "/api/v1"
VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(level=os.environ.get("FOCUSGUARD_LOG_LEVEL", "INFO"))
    get_engine()

    # Convenient for development and tests. Alembic owns the schema in
    # production, where AUTO_CREATE_TABLES is left off.
    if os.environ.get("FOCUSGUARD_AUTO_CREATE_TABLES", "1") == "1":
        create_all()
        logger.info("Schema ensured")

    logger.info("FocusGuard backend %s ready", VERSION)
    yield
    logger.info("FocusGuard backend shutting down")


app = FastAPI(
    title="FocusGuard API",
    version=VERSION,
    description=(
        "Attention and productivity analytics. Agents upload structured events; "
        "no video, screenshots or input text are accepted or stored."
    ),
    lifespan=lifespan,
)

# Development origins for the Vite dev server. Production origins come from the
# environment - a wildcard here would let any site call the API with a user's
# cookies once authentication exists.
_default_origins = "http://localhost:5173,http://127.0.0.1:5173"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get("FOCUSGUARD_CORS_ORIGINS", _default_origins).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(agent.router, prefix=API_PREFIX)
app.include_router(sessions.router, prefix=API_PREFIX)


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """Liveness check. Reports the database dialect, never its credentials."""
    return HealthResponse(
        status="ok", version=VERSION, database=database_url().split("://", 1)[0]
    )


def main() -> int:
    import uvicorn

    uvicorn.run(
        "focusguard_backend.main:app",
        host=os.environ.get("FOCUSGUARD_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("FOCUSGUARD_API_PORT", "8000")),
        reload=os.environ.get("FOCUSGUARD_API_RELOAD", "0") == "1",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
