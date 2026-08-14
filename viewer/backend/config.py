import os
from pathlib import Path

CATALOG_DB = os.environ.get("CATALOG_DB", "/app/state/catalog.sqlite")
STATE_DIR = os.environ.get("STATE_DIR", str(Path(CATALOG_DB).parent))
MATCH_RUNS_DIR = os.environ.get("MATCH_RUNS_DIR", str(Path(STATE_DIR) / "match-runs"))
ANALYSIS_CACHE_PATH = os.environ.get(
    "ANALYSIS_CACHE_PATH", str(Path(STATE_DIR) / "job-analysis-cache.json")
)

NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", "meta/llama-4-maverick-17b-128e-instruct")
NVIDIA_ENSEMBLE_SCORERS = os.environ.get("NVIDIA_ENSEMBLE_SCORERS")
NVIDIA_ENSEMBLE_SYNTHESIZER = os.environ.get("NVIDIA_ENSEMBLE_SYNTHESIZER")

MATCHER_DIR = os.environ.get("MATCHER_DIR", "/matcher")
PYTHON_BIN = os.environ.get("PYTHON_BIN", "python3")
CAREER_OPS_DIR = os.environ.get("CAREER_OPS_DIR", "career-ops")

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
SCORE_NOTIFY_MIN_SCORE = int(os.environ.get("SCORE_NOTIFY_MIN_SCORE", "4"))

PORT = int(os.environ.get("PORT", "3000"))

LOGO_DEV_PUBLISHABLE_KEY = os.environ.get("LOGO_DEV_PUBLISHABLE_KEY")
LOGO_DEV_SECRET_KEY = os.environ.get("LOGO_DEV_SECRET_KEY")
USER_LOCATION = os.environ.get("USER_LOCATION")

SAVED_SEARCH_ANALYZER_ENABLED = os.environ.get("SAVED_SEARCH_ANALYZER_ENABLED", "1") != "0"
SAVED_SEARCH_ANALYZER_INTERVAL_MS = int(
    os.environ.get("SAVED_SEARCH_ANALYZER_INTERVAL_MS", "60000")
)

SKIP_TIERS = os.environ.get("SKIP_TIERS", "intern")
