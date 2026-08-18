# Task runner for the Python viewer backend (viewer/backend/).
# Everything runs in Docker per this repo's "never run npm/python directly" policy.
#
# Usage: just <recipe>   (install https://github.com/casey/just)

set shell := ["bash", "-uc"]

viewer_backend := "viewer/backend"
viewer_image := "job-crawler-viewer-dev"
uid := `id -u`
gid := `id -g`

# Build (or reuse the cached) viewer backend dev image — deps + lint/test tooling
_viewer-image:
    docker build -q -t {{viewer_image}} -f {{viewer_backend}}/Dockerfile.dev {{viewer_backend}} > /dev/null

# Lint the viewer backend (ruff + black --check); fails on any violation
lint: _viewer-image
    docker run --rm -u {{uid}}:{{gid}} -v "$(pwd)/{{viewer_backend}}:/app" -w /app {{viewer_image}} \
        sh -c "ruff check . && black --check ."

# Auto-fix formatting/lint issues in the viewer backend (ruff --fix + black)
fmt: _viewer-image
    docker run --rm -u {{uid}}:{{gid}} -v "$(pwd)/{{viewer_backend}}:/app" -w /app {{viewer_image}} \
        sh -c "ruff check --fix . && black ."

# Run the viewer backend test suite with coverage (fails under 70%)
test: _viewer-image
    docker run --rm -u {{uid}}:{{gid}} -v "$(pwd)/{{viewer_backend}}:/app" -w /app {{viewer_image}} \
        python -m pytest --cov=. --cov-report=term-missing

# Run the viewer backend dev server with live reload, against real crawler state
dev: _viewer-image
    docker run --rm -it \
        -v "$(pwd)/{{viewer_backend}}:/app" \
        -v "$(pwd)/crawler/state:/app/state" \
        -v "$(pwd)/matcher:/matcher:ro" \
        -p 3000:3000 \
        -e CATALOG_DB=/app/state/catalog.sqlite \
        -e CAREER_OPS_DIR="${CAREER_OPS_DIR:-career-ops}" \
        -e LOGO_DEV_PUBLISHABLE_KEY="${LOGO_DEV_PUBLISHABLE_KEY:-}" \
        -e LOGO_DEV_SECRET_KEY="${LOGO_DEV_SECRET_KEY:-}" \
        -e DISCORD_WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}" \
        -e SCORE_NOTIFY_MIN_SCORE="${SCORE_NOTIFY_MIN_SCORE:-4}" \
        -e NVIDIA_API_KEY="${NVIDIA_API_KEY:-}" \
        -e NVIDIA_MODEL="${NVIDIA_MODEL:-meta/llama-4-maverick-17b-128e-instruct}" \
        -e NVIDIA_ENSEMBLE_SCORERS="${NVIDIA_ENSEMBLE_SCORERS:-}" \
        -e NVIDIA_ENSEMBLE_SYNTHESIZER="${NVIDIA_ENSEMBLE_SYNTHESIZER:-}" \
        {{viewer_image}}
