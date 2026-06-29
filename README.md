# ST. PRIDE Volunteer Management

ST. PRIDE Volunteer Management is an open-source, lightweight volunteer management application foundation. This milestone provides only the production-ready project scaffold for a future FastAPI application that can run in Azure Container Apps.

No volunteer management business logic, authentication flow, or Microsoft Entra ID integration is implemented yet.

## Technology Stack

- Python 3.13
- FastAPI and Uvicorn
- SQLAlchemy and Alembic, prepared for future database work
- SQLite with the default database path `/data/app.db`
- Jinja2 templates
- HTMX, Bootstrap 5, and minimal vanilla JavaScript when needed later
- Pydantic Settings for environment-driven configuration
- pytest, Ruff, Black, and pre-commit for development quality checks

## Local Development

1. Create and activate a virtual environment:

   ```bash
   python3.13 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements-dev.txt
   ```

3. Copy the example environment file if you want local overrides:

   ```bash
   cp .env.example .env
   ```

4. Run the application:

   ```bash
   uvicorn app.main:app --reload
   ```

5. Open the health endpoint:

   ```bash
   curl http://localhost:8000/healthz
   ```

   Expected response:

   ```json
   {"status":"ok","version":"0.1.0"}
   ```

## Docker Usage

Start the application with Docker Compose:

```bash
docker compose up --build
```

The application listens on `http://localhost:8000`. Docker Compose mounts a persistent named volume at `/data`, and the default SQLite database URL points to `/data/app.db`.

## Project Structure

```text
app/
  api/          Future API routers
  auth/         Future Microsoft Entra ID OpenID Connect integration
  config/       Pydantic Settings configuration
  database/     SQLAlchemy engine/session and base metadata
  forms/        Future form definitions/helpers
  mail/         Future email integration
  models/       Future SQLAlchemy models
  services/     Future application services
  static/       Static assets
  templates/    Jinja2 templates
  utils/        Shared utilities
migrations/     Alembic migration environment
tests/          pytest test suite
config/         Deployment/runtime configuration placeholders
scripts/        Maintenance and automation scripts
.github/        GitHub Actions workflows
```

## Quality Checks

```bash
ruff check .
black --check .
pytest
```
