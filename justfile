# Default recipe - runs the agent
default: run

# Run the agent
run:
    PYTHONPATH=src uv run python -m tachikoma

# Run tests
test *args:
    uv run pytest {{args}}

# Run linting
lint:
    uv run ruff check .

# Format code
fmt:
    uv run ruff format .

# Check types
typecheck:
    uv run ty check

# Install all dependencies
install:
    uv sync --all-groups

# Run all quality gates
check: lint typecheck test
