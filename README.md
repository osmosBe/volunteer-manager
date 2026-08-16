# ST. PRIDE Volunteer Manager

An open-source web application for planning events and coordinating volunteers—from public registration to shift assignment, briefing, check-in, materials, and operational communication.

The application was created for [ST. PRIDE](https://stpride.at/) and is designed so other organizations can run it without depending on ST. PRIDE infrastructure or Microsoft Azure. The user interface is currently German; setup and operations documentation is English.

[![CI](https://github.com/osmosBe/volunteer-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/osmosBe/volunteer-manager/actions/workflows/ci.yml)
[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FosmosBe%2Fvolunteer-manager%2Fmain%2Finfra%2Fazuredeploy.json)

## Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Choose a deployment](#choose-a-deployment)
- [Quick start: local development](#quick-start-local-development)
- [Docker Compose with SQLite](#docker-compose-with-sqlite)
- [Docker Compose with PostgreSQL](#docker-compose-with-postgresql)
- [Authentication](#authentication)
- [Permission management](#permission-management)
- [Transactional mail and Microsoft 365](#transactional-mail-and-microsoft-365)
- [Azure Container Apps](#azure-container-apps)
- [Database migrations and backups](#database-migrations-and-backups)
- [Diagnostics and troubleshooting](#diagnostics-and-troubleshooting)
- [Development and CI](#development-and-ci)
- [Security notes](#security-notes)

## Overview

ST. PRIDE Volunteer Manager provides one operational workspace for an event team and a privacy-conscious self-service flow for volunteers.

Volunteers can discover published events, select non-overlapping shifts, register, verify their email address, edit their registration through a random token, and cancel assignments. Administrators and managers can build event structures, manage capacities and waiting lists, review volunteers, assign shifts, prepare briefings, generate print/CSV views, and operate event-day check-in with QR codes and material hand-out/return tracking.

The application deliberately separates four concerns:

```text
Identity provider             Application                    Data and delivery
-----------------             -----------                    -----------------
EasyAuth or generic OIDC ---> AuthenticatedUser ----┐
                                                    ├─> permission engine -> routes/services
roles, groups, email -------> database mappings ----┘

business workflow ----------> MailService -----------> console or Microsoft Graph
SQLAlchemy/Alembic ----------> DATABASE_URL ----------> SQLite or PostgreSQL
```

That separation keeps the same application image usable in Azure Container Apps, Docker Compose, and Kubernetes. Azure EasyAuth, Microsoft Graph, PostgreSQL, and SQLite are provider choices rather than business-code dependencies.

### Current boundaries

- The UI is German; this README and operational documentation are English.
- SQLite is supported only for one application replica on a local disk.
- Generic OIDC login sessions are server-side but currently process-local; use one application replica until a shared session store is implemented.
- The `police` permission exists as a reserved placeholder and currently grants no routes.
- The provider-neutral Graph mail foundation and the existing SMTP/outbox workflow coexist. Existing workflows have not all been migrated to `MailService` yet.
- No local username/password database, bundled identity provider, Redis, Celery, or newsletter system is included.

## Features

### Volunteer experience

- Public event pages and multi-shift registration
- Capacity, waiting-list, age, and overlapping-shift validation
- Double opt-in email verification
- Token-protected registration editing and assignment cancellation
- Registration and assignment confirmation pages with QR codes

### Event operations

- Events, teams/areas, roles/tasks, shifts, and capacities
- Volunteer approval, rejection, assignment, and bulk actions
- Waiting-list promotion and briefing status
- Mobile event check-in, QR scan, check-out, and material tracking
- Print views for shift plans, check-in lists, and QR cards
- CSV exports for contacts and shifts

### Administration and platform

- Database-backed role, group, and email permission mappings
- Audited permission and configuration changes
- Configurable database-backed logo/branding
- Mail templates, legacy SMTP/outbox administration, and provider-neutral test mail
- Safe liveness, readiness, database, authentication, and mail diagnostics
- PostgreSQL and SQLite schema management through the same Alembic history

Demo data is fictional, uses only `example.invalid` addresses, and is never enabled by default outside the development deployment. Set `DEMO_MODE=true` to show a prominent warning on every HTML page. This visual mode is deliberately independent from `SEED_DEMO_DATA`: the former labels an environment, while the latter changes database contents.

## Architecture

### Application stack

- FastAPI and Uvicorn
- SQLAlchemy 2 and Alembic
- PostgreSQL via psycopg 3 or SQLite
- Jinja2 server-rendered UI
- Authlib for generic OpenID Connect
- MSAL plus Microsoft Graph REST for app-only mail delivery
- Docker/OCI image based on Python 3.13

### Azure delivery path

```text
GitHub dev branch
  -> GitHub Actions CI
  -> GitHub Actions OIDC (no Azure deployment client secret)
  -> Azure Container Registry
  -> Azure Container Apps revision
  -> Container Apps migration job: alembic upgrade head
  -> PostgreSQL Flexible Server
  -> exact commit SHA, liveness, and readiness verification
```

`DATABASE_URL` is the only database selector. Importing the application does not connect to a database, create tables, seed data, or run migrations. The web container starts Uvicorn only; migrations are an explicit deployment step.

## Choose a deployment

| Scenario               | Authentication | Database                   | Replicas           | Recommended for             |
| ---------------------- | -------------- | -------------------------- | ------------------ | --------------------------- |
| Local development      | `disabled`     | SQLite                     | 1                  | isolated development and CI |
| Simple self-hosted     | generic OIDC   | SQLite local volume        | 1                  | small installations         |
| Production self-hosted | generic OIDC   | PostgreSQL                 | 1 currently        | durable self-hosting        |
| Kubernetes             | generic OIDC   | PostgreSQL                 | 1 currently        | platform-managed deployment |
| Azure Container Apps   | Entra EasyAuth | PostgreSQL Flexible Server | existing DEV model | ST. PRIDE/Azure deployments |

The one-replica limitation for generic OIDC is caused by the current in-process server-side session store, not PostgreSQL. Horizontal scaling needs a shared session backend first.

## Quick start: local development

Requirements: Python 3.13 and a shell. Docker is optional.

```bash
git clone https://github.com/osmosBe/volunteer-manager.git
cd volunteer-manager
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. The example uses `AUTH_MODE=disabled`, which creates the local development identity and grants it all permissions.

> **Never expose `AUTH_MODE=disabled` to an untrusted network.** Outside development/test the application refuses to start unless `ALLOW_INSECURE_AUTH=true` is explicitly set.

Optional fictional demo data:

```bash
python -m scripts.seed_default_event
```

To mark a local installation visibly as a demo without changing its data:

```dotenv
DEMO_MODE=true
```

Never use the banner as a security boundary. It is presentation-only; authentication, authorization, mail delivery, and data retention continue to follow their normal configuration.

Useful checks:

```bash
curl --fail http://localhost:8000/healthz
curl --fail http://localhost:8000/health/live
curl --fail http://localhost:8000/health/ready
```

## Docker Compose with SQLite

This is the smallest self-hosted installation. It stores `/data/volunteer.db` in the named volume `volunteer-data` and does not need PostgreSQL or Azure.

```bash
cp .env.example .env
# Configure AUTH_MODE=oidc, APP_BASE_URL, SESSION_SECRET, and OIDC_* in .env.

docker compose -f deploy/docker-compose.sqlite.yml build
docker compose -f deploy/docker-compose.sqlite.yml run --rm volunteer-manager alembic upgrade head
docker compose -f deploy/docker-compose.sqlite.yml up -d
docker compose -f deploy/docker-compose.sqlite.yml ps
```

Operational rules:

- Run exactly one application replica.
- Keep the database on a local Docker volume or bind mount.
- Do not use SMB, NFS, Azure Files, or another network filesystem.
- Run migrations explicitly before starting an upgraded image.
- Back up with SQLite's online backup mechanism or while the container is stopped.

The port is configurable with `APP_PORT` and defaults to `8000`.

## Docker Compose with PostgreSQL

The PostgreSQL edition starts the application and PostgreSQL 17 with separate persistent storage. Protect `.env`; it contains deployment secrets and is gitignored.

```dotenv
POSTGRES_DB=volunteer
POSTGRES_USER=volunteer
POSTGRES_PASSWORD=<strong-raw-password>
DATABASE_URL=postgresql+psycopg://volunteer:<url-encoded-password>@postgres:5432/volunteer

AUTH_MODE=oidc
APP_BASE_URL=https://volunteer.example.org
SESSION_SECRET=<long-random-value>
OIDC_ISSUER_URL=https://auth.example.org/application/o/volunteer/
OIDC_CLIENT_ID=<client-id>
OIDC_CLIENT_SECRET=<client-secret>
```

Use the raw password for `POSTGRES_PASSWORD` and its URL-encoded form inside `DATABASE_URL`.

```bash
docker compose -f deploy/docker-compose.postgres.yml build
docker compose -f deploy/docker-compose.postgres.yml up -d postgres
docker compose -f deploy/docker-compose.postgres.yml run --rm volunteer-manager alembic upgrade head
docker compose -f deploy/docker-compose.postgres.yml up -d
docker compose -f deploy/docker-compose.postgres.yml ps
```

The application waits for PostgreSQL health before starting, but it deliberately does not run schema migrations automatically.

## Authentication

Select one provider centrally with `AUTH_MODE`:

| Mode       | Purpose                            | Configuration                            |
| ---------- | ---------------------------------- | ---------------------------------------- |
| `disabled` | isolated local development/CI only | no external identity provider            |
| `easyauth` | Azure Container Apps EasyAuth      | trusted `X-MS-CLIENT-PRINCIPAL` headers  |
| `oidc`     | generic OpenID Connect             | discovery, Authorization Code Flow, PKCE |

Every provider produces the same internal `AuthenticatedUser` with `user_id`, `name`, `email`, `roles`, `groups`, and `claims`. Routes and permission checks do not contain provider-specific role logic.

### Generic OIDC

The implementation uses standards-based discovery at `{OIDC_ISSUER_URL}/.well-known/openid-configuration`, Authorization Code Flow, state and nonce validation, and PKCE. The callback URI is derived from `APP_BASE_URL`:

```text
<APP_BASE_URL>/auth/callback
```

Example:

```dotenv
AUTH_MODE=oidc
APP_BASE_URL=https://volunteer.example.org
SESSION_SECRET=<long-random-secret>
OIDC_ISSUER_URL=https://auth.example.org/application/o/volunteer/
OIDC_CLIENT_ID=<client-id>
OIDC_CLIENT_SECRET=<client-secret>
OIDC_SCOPES="openid profile email"
OIDC_ROLE_CLAIMS="roles,realm_access.roles"
OIDC_GROUP_CLAIMS="groups"
OIDC_USER_ID_CLAIM="sub"
OIDC_NAME_CLAIM="name"
OIDC_EMAIL_CLAIMS="email,preferred_username"
```

Dotted paths such as `realm_access.roles` support Keycloak-style nested claims. The same generic adapter can be configured for Authentik, Keycloak, Zitadel, or direct Entra OIDC when the provider supports standard discovery.

Tokens exist only during callback processing and are never exposed to templates, diagnostics, or logs. The browser receives an opaque signed, `HttpOnly`, `SameSite=Lax` session cookie; HTTPS also enables `Secure`.

### Azure EasyAuth

Set `AUTH_MODE=easyauth`. Generic `OIDC_*` credentials are neither read nor required. Configure Entra authentication on the Container App and expose the appropriate App Roles/groups through EasyAuth. Existing login/logout routes remain `/auth/login` and `/auth/logout`.

## Permission management

The application database is the single source of truth for authorization. No YAML file is loaded or written at runtime.

Alembic creates the four system permissions and their initial App Role mappings idempotently:

| Permission | Initial role        | Effective access                                                                                  |
| ---------- | ------------------- | ------------------------------------------------------------------------------------------------- |
| `admin`    | `Volunteer.Admin`   | all application and technical administration                                                      |
| `manager`  | `Volunteer.Manager` | operational access except DB diagnostics, SMTP, permission management, and admin mail diagnostics |
| `checkin`  | `Volunteer.CheckIn` | event check-in, QR scan, check-in/check-out                                                       |
| `police`   | `Volunteer.Police`  | reserved; no current routes                                                                       |

Administrators manage mappings at `/admin/permissions`. A permission may match any of:

- an exact App Role;
- an exact group object ID (UUID); or
- a normalized email address as an emergency/fallback mapping.

App Roles are preferred. Group IDs are authoritative; optional labels are display-only. Authorization fails closed for database errors, missing permissions, and permissions without mappings. The last `admin` mapping cannot be removed. All mapping and metadata changes are audited.

## Transactional mail and Microsoft 365

New installations default to `MAIL_PROVIDER=console`. The console provider never sends a message and logs only safe metadata—never bodies, tokens, secrets, or attachment contents.

```text
business workflow -> MailService -> MailProvider
                                  -> ConsoleMailProvider
                                  -> MicrosoftGraphMailProvider
                                     -> GraphTokenProvider
```

The Graph provider uses app-only authentication and always sends through:

```text
POST /users/{MAIL_FROM_ADDRESS}/sendMail
```

It never uses `/me/sendMail`. The API request supports To/CC/BCC, HTML or text bodies, Reply-To, small file attachments, and Sent Items. The application requests only the Graph `Mail.Send` capability and does not read mailboxes.

### Quick step: configure Exchange Online Application RBAC

`Mail.Send` application access is powerful. An unscoped Entra grant permits sending as mailboxes across the tenant. The preferred modern design is a resource-scoped **Exchange Online Application RBAC** assignment. Microsoft documents that Entra app grants and Exchange RBAC grants are additive; remove an unscoped Entra `Mail.Send` grant when the scoped Exchange assignment is the intended authorization source.

Prerequisites:

- a shared mailbox;
- a dedicated Entra application and Enterprise Application/service principal;
- Exchange Online PowerShell;
- the application/client ID and the **service-principal Object ID** from Enterprise Applications (not the App Registration object ID).

Choose an organization-specific custom-attribute marker and verify it is not used by another mailbox:

```powershell
Connect-ExchangeOnline

$SharedMailbox = "shared-mailbox@example.org"
$ApplicationId = "<ENTRA-APPLICATION-CLIENT-ID>"
$ServicePrincipalObjectId = "<ENTERPRISE-APP-SERVICE-PRINCIPAL-OBJECT-ID>"
$ScopeName = "Volunteer Manager sender"
$ScopeMarker = "VolunteerManagerMailSender"

Set-Mailbox -Identity $SharedMailbox -CustomAttribute15 $ScopeMarker

New-ManagementScope `
  -Name $ScopeName `
  -RecipientRestrictionFilter "CustomAttribute15 -eq '$ScopeMarker'"

New-ServicePrincipal `
  -AppId $ApplicationId `
  -ObjectId $ServicePrincipalObjectId `
  -DisplayName "Volunteer Manager mail"

New-ManagementRoleAssignment `
  -Name "Volunteer Manager scoped Mail.Send" `
  -App $ServicePrincipalObjectId `
  -Role "Application Mail.Send" `
  -CustomResourceScope $ScopeName

Test-ServicePrincipalAuthorization `
  -Identity $ApplicationId `
  -Resource $SharedMailbox | Format-Table

Test-ServicePrincipalAuthorization `
  -Identity $ApplicationId `
  -Resource "known-unauthorized-mailbox@example.org" | Format-Table
```

The intended mailbox must report `InScope=True`; the negative test must report `InScope=False`. `Test-ServicePrincipalAuthorization` evaluates Exchange RBAC only and does not include separate Entra grants. Permission caches can take 30 minutes to two hours, so finish with a real negative Graph test after propagation.

References: [RBAC for Applications in Exchange Online](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac) and [Microsoft Graph `user: sendMail`](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0).

### Quick step: configure the runtime

Non-secret values:

```dotenv
MAIL_PROVIDER=graph
MAIL_FROM_ADDRESS=shared-mailbox@example.org
MAIL_FROM_NAME="Volunteer Team"
MAIL_REPLY_TO=volunteers@example.org
M365_TENANT_ID=<tenant-id>
M365_CLIENT_ID=<application-client-id>
M365_AUTH_MODE=client_secret
```

Secret:

```text
M365_CLIENT_SECRET=<client-secret>
```

In Azure, store it as the Container App secret `m365-client-secret` and expose it only through `M365_CLIENT_SECRET=secretref:m365-client-secret`. Never commit it or put it in a diagnostic endpoint.

Then:

1. Open `/admin/mail` as an administrator.
2. Confirm provider, sender, auth mode, and configured state.
3. Send a test to an explicitly supplied address.
4. Verify the shared mailbox Sent Items.
5. Verify the unauthorized-mailbox Graph test returns `403` after permission propagation.

Graph `202 Accepted` means Exchange accepted the request; it does not prove final delivery. Client secrets need an owner, expiry alert, and overlap-based rotation. `managed_identity` is reserved behind the token-provider abstraction but intentionally fails closed today.

## Azure Container Apps

### Quick step: provision a new environment

The Deploy to Azure button provisions the compiled `infra/azuredeploy.json`. `infra/main.bicep` and its modules are the source of truth. The template creates or explicitly reuses:

- Azure Container Registry;
- Log Analytics and Container Apps environment;
- bootstrap Container App and manual migration job;
- PostgreSQL Flexible Server and database;
- optional storage for future files/exports (never for SQLite/PostgreSQL database files).

For CLI use, review the change first and provide a strong PostgreSQL password through a secure parameter path:

```bash
az deployment group what-if \
  --resource-group <RESOURCE-GROUP> \
  --template-file infra/main.bicep \
  --parameters infra/parameters/dev.bicepparam

az deployment group create \
  --resource-group <RESOURCE-GROUP> \
  --template-file infra/main.bicep \
  --parameters infra/parameters/dev.bicepparam \
  postgresqlAdministratorPassword='<STRONG-PASSWORD>'
```

See [infra/README.md](infra/README.md) for existing-resource parameters and networking boundaries.

### Quick step: run `bootstrap.ps1`

Provision Azure first. Then authenticate Azure CLI and GitHub CLI and run the idempotent bootstrap:

```powershell
az login
gh auth login

pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

For repeatable administration:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 `
  -GitHubOwner "<OWNER>" `
  -GitHubRepository "<REPOSITORY>" `
  -AzureSubscriptionId "<SUBSCRIPTION-ID>" `
  -DevResourceGroup "<DEV-RESOURCE-GROUP>" `
  -AzureTenantId "<TENANT-ID>" `
  -EnvironmentName "development"
```

Bootstrap discovers unambiguous resource names, preserves matching GitHub OIDC and Container App secrets, and configures GitHub Environment variables. If a required resource or value is ambiguous, it stops instead of guessing.

The GitHub Actions federated credential must use:

```text
issuer:   https://token.actions.githubusercontent.com
subject:  repo:<owner>/<repository>:environment:development
audience: api://AzureADTokenExchange
```

Contributor on the selected Resource Group is sufficient for deployment but usually cannot create role assignments. If that operation fails, bootstrap prints the exact Resource Group-scoped command for an authorized administrator and does not escalate itself.

### DEV deployment behavior

A push to `dev` deploys only after all CI jobs pass. The reusable workflow builds the exact tested SHA, deploys it, runs Alembic in the migration job, optionally seeds fictional DEV data, and checks:

- exact commit SHA from `/healthz`;
- `/health/live`;
- PostgreSQL connectivity through `/health/ready`;
- successful migration-job execution.

The `production` GitHub Environment and `main` deployment remain separate; configure manual approval/required reviewers before enabling production delivery.

## Database migrations and backups

Alembic is the schema authority for both databases.

```bash
DATABASE_URL="sqlite:///./volunteer.db" alembic upgrade head
DATABASE_URL="postgresql+psycopg://.../volunteer?sslmode=require" alembic upgrade head
```

Do not run `Base.metadata.create_all()` in production and do not add migrations to application startup.

### SQLite backup

Prefer SQLite's online backup API:

```bash
sqlite3 /data/volunteer.db '.backup /backup/volunteer.db'
```

Alternatively stop the application before copying the file. Never blindly copy a database while it is being written.

### PostgreSQL backup

Use a consistent dump and test restores:

```bash
pg_dump -Fc -h <host> -U <user> volunteer > volunteer.dump
createdb volunteer_restore_test
pg_restore -d volunteer_restore_test volunteer.dump
```

## Diagnostics and troubleshooting

### Safe endpoints

| Endpoint             | Purpose                                    | Access            |
| -------------------- | ------------------------------------------ | ----------------- |
| `/healthz`           | health plus deployed version/SHA           | public            |
| `/health/live`       | process liveness                           | public            |
| `/health/ready`      | database readiness                         | public            |
| `/admin/db`          | Alembic revision and required-table status | admin             |
| `/admin/mail`        | provider status and test mail              | admin             |
| `/admin/permissions` | permission mappings                        | admin             |
| `/debug/config`      | safe configuration flags                   | `DEBUG=true` only |
| `/debug/db`          | safe database status                       | `DEBUG=true` only |
| `/debug/easyauth`    | safe EasyAuth diagnostics                  | `DEBUG=true` only |
| `/debug/oidc`        | safe OIDC diagnostics                      | `DEBUG=true` only |
| `/debug/mail`        | safe mail configuration flags              | `DEBUG=true` only |

Debug endpoints return `404` when `DEBUG=false`. They never expose database URLs, client secrets, access/ID/refresh tokens, Graph bearer tokens, raw authorization responses, or a full environment dump.

### Common problems

#### OIDC redirect or state failure

- Confirm `APP_BASE_URL` exactly matches the externally visible HTTPS origin.
- Register `<APP_BASE_URL>/auth/callback` at the provider.
- Confirm reverse-proxy scheme/host handling and that one process handles the session.

#### EasyAuth login works but access is denied

- Confirm `AUTH_MODE=easyauth` and the Container App issuer/audience.
- Inspect safe `/debug/easyauth` output with `DEBUG=true`.
- Check role/group/email mappings at `/admin/permissions`.

#### Database schema is pending

- Inspect `/admin/db`.
- Run `alembic upgrade head` using the same `DATABASE_URL` as the application.
- In Azure, inspect the migration-job execution; the web container will not repair schema automatically.

#### Azure OIDC subject mismatch

- Confirm the workflow uses the `development` GitHub Environment.
- Confirm the federated subject exactly matches `repo:<owner>/<repository>:environment:development`.

#### DEV returns an old revision

- The deployment workflow requires `/healthz` to return the exact tested SHA, not merely HTTP 200.
- Inspect the latest Container App revision and workflow diagnostic logs.

#### Graph returns 403

- Confirm the configured sender exactly matches the mailbox in the Exchange scope.
- Confirm `Application Mail.Send` is assigned to the correct Enterprise Application service principal.
- Re-run `Test-ServicePrincipalAuthorization`; allow for permission-cache propagation.

## Development and CI

Run the complete local gate before opening a pull request:

```bash
black .
black --check .
ruff check .
pytest
```

Validate Compose files when Docker Compose is available:

```bash
docker compose -f deploy/docker-compose.sqlite.yml config
docker compose -f deploy/docker-compose.postgres.yml config
```

GitHub Actions additionally verifies repeatable SQLite migrations, migrations and model semantics against a disposable PostgreSQL 16 service, Bicep compilation, and PowerShell parsing. Tests require no Azure, Microsoft 365, external OIDC provider, production PostgreSQL, or real secrets.

### Repository layout

```text
app/                    FastAPI routes, auth, models, services, templates
app/auth/               disabled, EasyAuth, and generic OIDC adapters
app/mail/               provider-neutral mail models/service/providers
deploy/                 SQLite and PostgreSQL Docker Compose editions
infra/                  Bicep source, modules, parameters, compiled ARM JSON
migrations/             shared Alembic schema history
scripts/bootstrap.ps1   idempotent Azure/GitHub/OIDC bootstrap
scripts/deploy_database.py
                        deployment-time migration and optional DEV seed
tests/                  SQLite tests and PostgreSQL integration tests
.github/workflows/      CI and post-CI DEV deployment
```

Architecture rationale is recorded in [DECISIONS.md](DECISIONS.md). [DEMO.md](DEMO.md) contains a product walkthrough.

## Security notes

- Never expose `AUTH_MODE=disabled` to an untrusted network.
- Protect `.env`, OIDC client secrets, PostgreSQL credentials, and Graph credentials; never bake them into an image.
- Keep `DEBUG=false` in public deployments.
- Use HTTPS for OIDC and all public deployments.
- Restrict Microsoft 365 application mail access at Exchange Online level and prove the negative mailbox test.
- Keep SQLite on local storage and at one replica.
- Treat public registration edit links and QR links as bearer-style secrets.
- Review reverse-proxy headers, request-size limits, TLS, backups, restore tests, and update procedures before production use.
- Existing POST forms rely on same-site authentication boundaries; dedicated CSRF tokens remain a recommended hardening milestone for broader Internet deployments.

## License

This project is licensed under the [MIT License](LICENSE).
