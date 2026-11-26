"""FocusGuard backend API (Phase 12+)."""

__version__ = "0.1.0"

# Read .env before any backend module consults the environment: the JWT secret
# and token lifetimes, the database path and the SMTP settings all live there.
# This runs before every submodule import, so nothing reads the environment
# too early. Real environment variables win (override=False), so a deployment
# can still set them directly.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=False)
