import os
from pathlib import Path

CATALOG_DB = os.environ.get("CATALOG_DB", "/app/state/catalog.sqlite")
STATE_DIR = os.environ.get("STATE_DIR", str(Path(CATALOG_DB).parent))
MATCH_RUNS_DIR = os.environ.get("MATCH_RUNS_DIR", str(Path(STATE_DIR) / "match-runs"))
