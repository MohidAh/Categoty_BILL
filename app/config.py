"""Configuration and path management for BillBook."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# v6.0 Phase 2: honor BILLBOOK_DATA_DIR env so the packaged sidecar can
# keep data/ next to the executable instead of inside the package dir.
_data_env = os.getenv("BILLBOOK_DATA_DIR", "")
DATA = Path(_data_env) if _data_env else BASE / "data"
UPLOADS = DATA / "uploads"
PAGES = DATA / "pages"
BACKUPS = DATA / "backups"

# Ensure directories exist
for d in (DATA, UPLOADS, PAGES, BACKUPS):
    d.mkdir(parents=True, exist_ok=True)

# App settings (read from env at runtime)
APP_PASSWORD_HASH = os.getenv("APP_PASSWORD_HASH", "")
# v8.13.3: Removed dead SESSION_SECRET (was never read by any Python code).
# If JWT/signed-cookie sessions are added in the future, generate a random
# secret at first boot via os.urandom(32).hex() and store in the settings table.

# Pagination defaults
PAGE_SIZE = 25

# v8.13.3: Named constants for magic numbers used across the codebase.
# Previously these were hardcoded in 10+ files.
DEFAULT_TREND_WINDOW_DAYS = 30    # Used by trend alerts, daily COGS avg, stock reserve
STOCK_RESERVE_TARGET_DAYS = 15     # Default target days-of-cover for stock reserve
BUSINESS_RESERVE_PCT = 10          # Default % of gross profit to reserve
LOGIN_THROTTLE_WINDOW_SEC = 60     # Per-IP login throttle window
LOGIN_THROTTLE_MAX_ATTEMPTS = 5   # Max failed attempts per window
PAIRING_CODE_LENGTH = 8           # v8.13.2: 8-digit pairing codes (was 6)
PAIRING_CODE_EXPIRY_MIN = 2       # v8.13.2: 2-minute expiry (was 5)
HTTP_SESSION_MAX_HOURS = 8        # v8.13.2: Max session duration over HTTP (LAN mode)
