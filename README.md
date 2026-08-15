# ST. PRIDE Volunteer Manager

## Implementierungsstand

Der aktuelle Entwicklungsstand enthält das persistente Fachmodell sowie den
getesteten Service für öffentliche Anmeldungen. Dieser prüft Anmeldezeiträume,
offene Schichten, doppelte Auswahl, zeitliche Überschneidungen, Kapazität und
Warteliste. Jede Anmeldung erhält einen kryptographisch zufälligen
Bearbeitungs-Token; in der Datenbank wird ausschließlich dessen SHA-256-Hash
gespeichert. Bestätigungs- und Stornierungsnachrichten werden als persistente
Outbox-Vorschau angelegt und nicht versendet.

Die HTTP-Oberfläche für die öffentliche Anmeldung ist unter
`/veranstaltungen/<slug>/anmeldung` vorhanden. Sie benötigt eine als öffentlich
freigegebene Veranstaltung mit offenen Schichten. Nach dem Absenden führt die
Bestätigungsseite zum individuellen Bearbeitungslink; einzelne Zuteilungen
können dort storniert werden. Die vollständige Bearbeitung von Kontaktdaten und
Schichtauswahl folgt in der nächsten Etappe.

ST. PRIDE Volunteer Management is an open-source, lightweight volunteer management application foundation. This milestone provides only the production-ready project scaffold for a future FastAPI application that can run in Azure Container Apps.

Die Anwendung befindet sich im Prototyping. Die vorhandene Azure Container Apps
EasyAuth-Integration bleibt erhalten; `AUTH_MODE=disabled` ist ausschließlich
für die lokale bzw. ausdrücklich freigegebene Prototyp-Entwicklung gedacht.
Der aktuelle Stand ist nicht als öffentliches Produktivsystem freigegeben.

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


## Azure Container Apps DEV Deployment

The DEV GitHub Actions deployment keeps the existing Azure Container Apps deployment flow and then checks the deployed app health endpoint. Configure a GitHub environment secret named `DEV_APP_URL` on the `development` environment with the public base URL of the DEV app, for example `https://example-dev.example.azurecontainerapps.io`. The workflow calls `${DEV_APP_URL}/healthz` after deployment and fails unless the endpoint returns HTTP 200.

Azure Container App ingress must target port `8000`, which is the port exposed by the application container. Before enabling SQLite-backed features, configure persistent storage mounted at `/data` so the default SQLite database path `/data/app.db` is durable across container restarts and revisions.

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


## Database and Migrations

The application uses SQLite through SQLAlchemy. Runtime configuration comes from `DATABASE_URL` in `Settings`; by default it points at `sqlite:////data/app.db`, so containers should mount persistent storage at `/data`. The application does not create tables on production startup. Apply Alembic migrations before serving traffic.

Run migrations locally:

```bash
alembic upgrade head
```

Run migrations against a local override database:

```bash
DATABASE_URL=sqlite:///./local.db alembic upgrade head
```

Run migrations in the container before starting or as an operational command:

```bash
docker compose run --rm app alembic upgrade head
```

Before deployment, run `alembic upgrade head` as a release/pre-start step against the persistent `/data/app.db` volume, then start the application container. This keeps schema changes explicit and avoids unsafe auto-create behavior in production.

Create the default draft event idempotently after migrations:

```bash
python -m scripts.seed_default_event
```

The default seed creates `St. Pölten PRIDE 2026` with slug `pride-2026` only when there are no events.

## Quality Checks

```bash
ruff check .
black --check .
pytest
```

## Microsoft Entra ID Admin Login with Azure Container Apps EasyAuth

Milestone 2 uses Azure Container Apps built-in Authentication / Authorization (EasyAuth) for Microsoft Entra ID sign-in. Azure Portal manages the Entra App Registration and its client secret. The FastAPI application does not implement OpenID Connect itself and does not require `ENTRA_CLIENT_SECRET` for admin login.

Azure authenticates the user before the request reaches the app. The application then performs app-level authorization with a small auth-provider abstraction that supports `AUTH_MODE=disabled` for local development and `AUTH_MODE=easyauth` for Azure EasyAuth headers. The rest of the app uses auth helpers instead of parsing headers directly.

The public `/healthz` and `/` routes remain unauthenticated. The `/admin` route is protected by app-level authorization.

> **Security warning:** EasyAuth headers must never be trusted unless the application is deployed behind Azure Container Apps Authentication / Authorization. Set `AUTH_MODE=easyauth` only in that protected Azure runtime.

### Runtime Configuration

All application configuration is loaded through the central Pydantic Settings class. Do not read application configuration directly from `os.getenv` in business code.

Required runtime variables:

| Variable | Description |
| --- | --- |
| `APP_BASE_URL` | Public base URL for the running app. |
| `AUTH_MODE` | Use `easyauth` in Azure Container Apps. Use `disabled` only for local development. |
| `DEBUG` | Enables temporary DEV troubleshooting endpoints when `true`. Keep unset or `false` in production. |
| `ADMIN_ALLOWED_EMAILS` | Comma-separated administrator email allowlist. Useful for DEV. |
| `ADMIN_ALLOWED_GROUP_IDS` | Comma-separated administrator Entra group object ID allowlist. |

Admin access is allowed when `AUTH_MODE=disabled`, or when the authenticated EasyAuth user matches `ADMIN_ALLOWED_EMAILS` or `ADMIN_ALLOWED_GROUP_IDS`. If neither allowlist is configured in `AUTH_MODE=easyauth`, admin access is denied by default.

### Local Development

For local development without Azure EasyAuth, set:

```bash
AUTH_MODE=disabled
APP_BASE_URL=http://localhost:8000
DEBUG=true
ADMIN_ALLOWED_EMAILS=
ADMIN_ALLOWED_GROUP_IDS=
```

Then run the app normally and open `/admin`. The app will show the synthetic local user `Local Developer` (`local-dev@stpride.local`). Do not use `AUTH_MODE=disabled` in Azure or any shared environment.

### DEV EasyAuth Troubleshooting

`GET /debug/easyauth` is available only when `DEBUG=true`. It returns sanitized identity information for troubleshooting EasyAuth header delivery:

- authenticated: `true` or `false`
- name
- email
- user_id
- groups
- claim names only
- auth_mode

It never returns raw headers, tokens, or full claim values. Keep `DEBUG=false` or unset outside DEV; `/debug/easyauth` returns `404` when debug mode is disabled.

### Azure Container Apps Setup

1. Enable Authentication on the Azure Container App.
2. Add Microsoft Entra ID as the identity provider in the Container Apps Authentication settings.
3. Configure redirect/callback settings according to Azure Container Apps Authentication requirements. EasyAuth owns the Microsoft Entra sign-in flow and Azure Portal manages the app registration secret.
4. Set `AUTH_MODE=easyauth` for the app container.
5. Keep `DEBUG=false` or unset except during temporary DEV troubleshooting.
6. Configure at least one of `ADMIN_ALLOWED_EMAILS` or `ADMIN_ALLOWED_GROUP_IDS`. Email allowlists are acceptable for DEV; group object IDs are preferred for shared environments.
7. If using group-based authorization, enable group claims for the identity provider/token configuration. Groups may not always be present in EasyAuth claims; this milestone only evaluates groups when Azure includes them in the EasyAuth principal claims. Microsoft Graph group lookup can be added later.

Example runtime environment configuration:

```bash
az containerapp update \
  --name ca-stpride-volunteer-dev \
  --resource-group rg-stpride-volunteer-dev \
  --set-env-vars \
    APP_BASE_URL="https://example.azurecontainerapps.io" \
    AUTH_MODE=easyauth \
    DEBUG=false \
    ADMIN_ALLOWED_EMAILS="admin@example.org" \
    ADMIN_ALLOWED_GROUP_IDS=""
```

GitHub Secrets are for CI/CD deployment credentials only. Application runtime authorization settings belong in Azure Container App environment variables or secrets as appropriate.


## Milestone 2 Authentication and Authorization

Authentication is handled by Azure Container Apps built-in Authentication / Authorization (EasyAuth) with Microsoft Entra ID. Azure authenticates users before requests reach FastAPI; the app trusts EasyAuth headers only when `AUTH_MODE=easyauth` is enabled in that protected Azure runtime. The FastAPI application then performs application-level authorization.

Public routes remain public: `GET /healthz` and `GET /`. The admin dashboard at `GET /admin` requires the `admin` permission through the shared permissions system.

### Authorization model

The preferred long-term authorization model is Microsoft Entra App Roles. The supported fallback/alternative is Microsoft Entra group claims using stable Entra Group Object IDs. Email allowlists are retained only as a deprecated DEV/emergency fallback for the `admin` permission.

The central mapping lives in `config/permissions.yaml`. For now, changes require updating the file and redeploying/restarting the application. A future milestone may make this editable through the GUI.

### App Role setup

In Microsoft Entra admin center, open the App Registration and choose **App roles → Create app role**.

Example administrator role:

- Display name: Volunteer Administrator
- Allowed member types: Users/Groups
- Value: `Volunteer.Admin`
- Description: Can fully administer the ST. PRIDE Volunteer Management application.
- Enabled: true

Assign users or groups through **Enterprise Applications → volunteer-app-admin-login → Users and groups → Add user/group → select role**.

Direct user assignment works. Group assignment may require Microsoft Entra ID P1 depending on the tenant plan. If using group claims instead of app roles, configure **Token configuration → Add groups claim → Group ID**. Do not use sAMAccountName for cloud authorization. Do not enable **Emit groups as role claims** unless intentionally mixing role and group concepts.

### Local development and debugging

Use `AUTH_MODE=disabled` for local development. The local developer has all permissions in this mode.

Set `DEBUG=true` only in DEV to enable `GET /debug/easyauth`. The endpoint returns sanitized identity and permission information only: authentication status, name, email, user ID, roles, groups, claim names, auth mode, and per-permission booleans. It does not expose raw headers, generic claim values, tokens, or secrets. Keep `DEBUG=false` or unset in production.

### Relevant settings

All application configuration is loaded through the central Settings class; do not call `os.getenv` elsewhere in application code.

- `APP_BASE_URL`
- `AUTH_MODE`
- `DEBUG`
- `PERMISSIONS_CONFIG_PATH`
- `ADMIN_ALLOWED_EMAILS` (deprecated DEV/emergency fallback for `admin`)
- `ADMIN_ALLOWED_GROUP_IDS` (deprecated DEV/emergency fallback for `admin`)
