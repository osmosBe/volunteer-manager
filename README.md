# ST. PRIDE Volunteer Manager

Open-source volunteer planning, registration, briefing, check-in, material and
communication management built with FastAPI. The user interface is currently
German; deployment and operations documentation is English for reuse by other
organizations.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FosmosBe%2Fvolunteer-manager%2Fmain%2Finfra%2Fazuredeploy.json)

The button provisions Azure resources from the compiled `infra/azuredeploy.json`
template. `infra/main.bicep` and its modules are the source of truth. Azure
provisioning cannot configure a GitHub repository or Entra EasyAuth by itself;
run `scripts/bootstrap.ps1` afterwards.

## What the application covers

- Public event pages and multi-shift registration with capacity, waiting lists,
  overlap and age checks
- Double opt-in email verification and token-protected registration editing
- Event, team, role, shift, volunteer, briefing and material administration
- Bulk status/briefing actions, controlled rejection and waiting-list promotion
- Mobile and QR check-in, material issue/return tracking and print views
- Search, filters, CSV exports, audit records, mail templates, SMTP and outbox
- Microsoft Entra EasyAuth integration without a second application login system
- Health, readiness and safe database/migration diagnostics

Demo data is explicitly fictional and uses only `example.invalid` addresses.
It is never enabled by default outside the development deployment.

## Architecture

```text
GitHub (dev/main)
  -> GitHub Actions CI
  -> GitHub Actions OIDC (no Azure client secret)
  -> Azure Container Registry
  -> Azure Container Apps revision
  -> Azure Container Apps migration job (same image, alembic upgrade head)
  -> Azure Database for PostgreSQL Flexible Server (TLS required)
```

Production and shared Azure deployments use PostgreSQL. SQLite remains supported
for lightweight local development and unit tests:

```text
sqlite:///./volunteer.db
postgresql+psycopg://USER:PASSWORD@HOST:5432/volunteer?sslmode=require
```

`DATABASE_URL` is the only application database selector. PostgreSQL uses
psycopg 3. SQLite PRAGMAs are applied only to SQLite connections. Importing the
application does not connect to a database, create schema objects or run
migrations.

## Local development

Requirements: Python 3.13 (the code remains compatible with the current local
test environment), and optionally Docker Compose.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

To add fictional local data after migrating:

```bash
python -m scripts.seed_default_event
```

With Docker Compose, the one-shot `migrate` service upgrades the local SQLite
volume before the web service starts:

```bash
docker compose up --build
```

The web container itself only runs Uvicorn. It never migrates or seeds a database
on startup.

## Azure quick start for a new fork

### 1. Fork or clone

Fork this repository, or clone it and push it to a GitHub repository you control.
Keep the long-term branch model to `dev` and `main`; no permanent test branch is
required.

### 2. Provision Azure

Use the **Deploy to Azure** button above. Choose an existing or new Resource
Group and provide a strong PostgreSQL administrator password when prompted.

The template creates, or can safely reuse by explicit name:

- Azure Container Registry (Basic)
- Log Analytics workspace
- Container Apps environment
- Container App bootstrap resource
- manual Container Apps Alembic migration job
- PostgreSQL Flexible Server 16, database `volunteer`, Burstable `Standard_B1ms`,
  32 GiB storage, seven-day backup, no HA or geo redundancy
- optional storage account for future files/exports (never for the database)

The PostgreSQL password is passed as a secure deployment parameter and stored in
Container App/Job secrets. It is not an output and is never committed.

For CLI deployment and existing-resource parameters, see [infra/README.md](infra/README.md).

### 3. Bootstrap GitHub and OIDC

Install and authenticate Azure CLI (`az`) and GitHub CLI (`gh`). **Provision the
Azure template from step 2 before running bootstrap.** Bootstrap deliberately
does not create PostgreSQL, a Container Apps migration Job, or an ACR image from
guessed values. This keeps an existing deployment safe.

For a first setup, the recommended flow is interactive. It makes every selected
Azure resource visible and works equally well for a new fork and an existing
deployment:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

Supply the GitHub owner/repository, Azure subscription ID, DEV Resource Group
and tenant ID when prompted. The remaining Azure resource names are detected
when they are unambiguous. A fully parameterized invocation is also supported
for repeatable administration:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 `
  -GitHubOwner "<OWNER>" `
  -GitHubRepository "<REPOSITORY>" `
  -AzureSubscriptionId "<SUBSCRIPTION-ID>" `
  -DevResourceGroup "<DEV-RESOURCE-GROUP>" `
  -AzureTenantId "<TENANT-ID>" `
  -EnvironmentName "development"
```

#### Bootstrap prompt guide

1. **Azure Container Apps migration job name**: after the Bicep deployment,
   bootstrap normally discovers the one migration Job automatically, so this
   prompt is not shown. If it is shown, enter the name returned by the following
   command. With the template defaults, it is `volunteer-dev-migrate`:

   ```powershell
   az containerapp job list --resource-group "<DEV-RESOURCE-GROUP>" --query "[].name" --output tsv
   ```

   If no Job is returned, stop with `Ctrl+C` and complete the Azure template
   deployment first. Do not enter a legacy placeholder such as
   `migrate-container-app`.

2. **PostgreSQL `DATABASE_URL`**: this is requested only when the existing
   Container App or migration Job lacks its `database-url` secret reference.
   Azure cannot read a secret value back from one resource to copy it to the
   other, so bootstrap asks once for the same value that was used to provision
   PostgreSQL. Its required form is:

   ```text
   postgresql+psycopg://<ADMIN-LOGIN>:<URL-ENCODED-PASSWORD>@<SERVER-FQDN>:5432/volunteer?sslmode=require
   ```

   Find the server host with:

   ```powershell
   az postgres flexible-server list --resource-group "<DEV-RESOURCE-GROUP>" --query "[].{Name:name,Host:fullyQualifiedDomainName}" --output table
   ```

   Use the administrator password chosen during template deployment. It cannot
   be recovered from Azure. URL-encode passwords containing special characters;
   do not paste the password into GitHub, source files or shell history.
   A blank URL is rejected before bootstrap changes an Azure secret.

3. **Azure application (Client) ID**: if `AZURE_CLIENT_ID` already exists only
   as a GitHub *Secret*, GitHub does not permit bootstrap to read it. Enter the
   client ID of the **GitHub Actions OIDC application**, not the application ID
   used by Container Apps EasyAuth. Find candidates and verify the federated
   credential subject:

   ```powershell
   az ad app list --all --query "[?contains(displayName, 'volunteer')].{Name:displayName,ClientId:appId}" --output table
   az ad app federated-credential list --id "<CLIENT-ID>" --query "[].{Name:name,Subject:subject,Issuer:issuer}" --output table
   ```

   The correct credential has the subject
   `repo:<owner>/<repository>:environment:development`. A blank client ID
   aborts before bootstrap writes GitHub Environment Variables; the existing
   GitHub Secret is never overwritten.

The script is idempotent. It discovers unambiguous Azure resources, preserves a
matching OIDC application/federated credential, creates only missing GitHub
configuration and updates values rather than replacing environments. If an
existing client ID is stored only as a write-only GitHub secret, the script asks
for that non-secret ID and leaves the secret untouched.

If `DATABASE_URL` secret references already exist, they are reused. If they are
missing, the script asks for the PostgreSQL URL with hidden input and adds only
the named secret/reference; it does not replace other Container App settings.

If no OIDC application exists, the script can create one without a client
secret. The preferred federated identity is:

```text
issuer:   https://token.actions.githubusercontent.com
subject:  repo:<owner>/<repository>:environment:development
audience: api://AzureADTokenExchange
```

Contributor at Resource Group scope is sufficient for deployments but may not
be sufficient to create role assignments. If RBAC assignment fails, bootstrap
prints the exact Resource Group-scoped `az role assignment create` command for a
permitted administrator and exits without attempting privilege escalation.

### 4. Configure Entra EasyAuth

Configure Microsoft Entra authentication on the Container App and set the
application allowlists (`ADMIN_ALLOWED_EMAILS` and/or
`ADMIN_ALLOWED_GROUP_IDS`). The application consumes trusted EasyAuth headers;
do not add a parallel password login.

`AUTH_MODE=disabled` is for isolated local development only. Shared DEV and
production environments should use `AUTH_MODE=easyauth`.

### 5. Configure transactional mail (optional)

New installations use `MAIL_PROVIDER=console`, which never contacts an external
mail service. It logs only provider/operation, recipient addresses/count and
subject; bodies, attachment contents and credentials are excluded. Set a valid
`MAIL_FROM_ADDRESS` before using the admin test page. Microsoft 365 is optional
and the application starts normally without Graph configuration.

The reusable boundary is:

```text
business workflow -> MailService -> MailProvider
                                  -> ConsoleMailProvider
                                  -> MicrosoftGraphMailProvider
                                     -> GraphTokenProvider
```

The legacy SMTP/outbox workflow is not migrated in this milestone. New business
workflows should depend on `MailService`; a later database outbox can be inserted
in front of it without changing provider code. No SMTP AUTH, Redis, Celery or
background queue is introduced here.

#### Microsoft 365 shared-mailbox setup

1. Create or select the shared mailbox. Record its primary SMTP address and
   Exchange alias; neither is hardcoded in the application.
2. Create a dedicated single-tenant Entra application and its service principal.
   Record the tenant ID, application/client ID and the **Enterprise application
   service-principal Object ID** (not the App Registration Object ID).
3. The only sending capability is `Mail.Send` (application). Do not grant
   `Mail.Read`, `Mail.ReadWrite`, `MailboxSettings.Read`, `User.Read.All` or a
   delegated permission. Graph delivery uses
   `POST /users/{MAIL_FROM_ADDRESS}/sendMail`, never `/me/sendMail`, with
   `https://graph.microsoft.com/.default`. In the traditional Entra flow this is
   **API permissions → Microsoft Graph → Application permissions → Mail.Send →
   Grant admin consent**. That grant is tenant-wide unless constrained by a
   legacy Application Access Policy; do not leave it in place alongside the
   preferred Exchange Application RBAC assignment described next.
4. Restrict the app to the intended mailbox **before installing its credential**.
   The preferred modern design is Exchange Online Application RBAC. The current
   Microsoft model has an important subtlety: an Entra-admin-consented
   organization-wide `Mail.Send` permission and an Exchange-scoped RBAC role are
   additive. Leaving both assigned defeats the RBAC mailbox restriction. For the
   preferred design, grant the scoped Exchange role below and remove any
   unscoped Entra `Mail.Send` app-role assignment before production use. This is
   the modern replacement for Application Access Policies.
5. Create a client secret only for the initial credential model. Copy it once,
   store it directly as the Container App secret `m365-client-secret`, then
   discard the plaintext. Never place it in GitHub variables, Bicep parameter
   files, commands retained in shell history or logs.
6. Configure the Container App values listed below, open `/admin/mail`, and send
   one test to an explicitly entered address.
7. Verify the message in the shared mailbox Sent Items. Graph `202 Accepted`
   means Exchange accepted the request, not that final delivery is guaranteed.
8. Verify the scoped authorization returns `InScope=True` for the sender mailbox
   and `InScope=False` for a known unauthorized mailbox. After RBAC cache
   propagation, a direct Graph send targeting that unauthorized mailbox must
   return 403. Never temporarily change the application's configured sender to a
   real person's mailbox for this test.

Microsoft documents the endpoint and `202`/Sent Items behavior in
[user: sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)
and the authorization model in
[RBAC for Applications in Exchange Online](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac).

**Security warning:** `Mail.Send` application access is powerful. An unscoped
Entra grant permits app-only sending as mailboxes across the tenant. The
application cannot enforce tenant-side mailbox isolation; Exchange Online must.

One resource-scope pattern for a dedicated mailbox is an Exchange custom
attribute. Choose an organization-specific marker and confirm it matches only
the intended mailbox:

```powershell
Connect-ExchangeOnline

Set-Mailbox -Identity "<SHARED-MAILBOX>" `
  -CustomAttribute15 "VolunteerManagerMailSender"

New-ManagementScope -Name "Volunteer Manager sender" `
  -RecipientRestrictionFilter "CustomAttribute15 -eq 'VolunteerManagerMailSender'"

New-ServicePrincipal `
  -AppId "<M365-CLIENT-ID>" `
  -ObjectId "<ENTERPRISE-APP-SERVICE-PRINCIPAL-OBJECT-ID>" `
  -DisplayName "Volunteer Manager mail"

New-ManagementRoleAssignment `
  -Name "Volunteer Manager scoped Mail.Send" `
  -App "<ENTERPRISE-APP-SERVICE-PRINCIPAL-OBJECT-ID>" `
  -Role "Application Mail.Send" `
  -CustomResourceScope "Volunteer Manager sender"

Test-ServicePrincipalAuthorization `
  -Identity "<M365-CLIENT-ID>" `
  -Resource "<SHARED-MAILBOX>" | Format-Table

Test-ServicePrincipalAuthorization `
  -Identity "<M365-CLIENT-ID>" `
  -Resource "<UNAUTHORIZED-MAILBOX>" | Format-Table
```

Review the scope's recipient preview and ensure no other mailbox carries the
same marker. `Test-ServicePrincipalAuthorization` tests Exchange RBAC only; it
does not include separate Entra grants. Remove any tenant-wide Entra
`Mail.Send` consent, wait for permission cache propagation (Microsoft documents
30 minutes to two hours), and perform the negative Graph test as the final proof.

#### Runtime configuration

Container App non-secret settings:

```text
MAIL_PROVIDER=graph
MAIL_FROM_ADDRESS=<SHARED-MAILBOX-ADDRESS>
MAIL_FROM_NAME=<DISPLAY-NAME>
MAIL_REPLY_TO=<OPTIONAL-ADDRESS>
M365_TENANT_ID=<TENANT-ID>
M365_CLIENT_ID=<MAIL-APP-CLIENT-ID>
M365_AUTH_MODE=client_secret
```

Secret/reference:

```text
Container App secret: m365-client-secret=<CLIENT-SECRET>
Environment:          M365_CLIENT_SECRET=secretref:m365-client-secret
```

`infra/main.bicep` exposes all values and treats `m365ClientSecret` as secure.
For an existing Container App, `scripts/bootstrap.ps1` preserves an existing
secret reference, prompts for a missing secret with hidden input, and redacts
native CLI output on secret-write failures. It does not create the shared
mailbox, Graph permission or Exchange RBAC assignment.

The client secret needs an owner and expiry alert. Rotate it by creating a second
credential, updating `m365-client-secret`, sending an admin test, and only then
deleting the old credential. Keep the overlap short. `M365_AUTH_MODE=managed_identity`
is reserved behind `GraphTokenProvider` but intentionally fails closed today;
Managed Identity is the preferred future credential model.

#### Test mail and safe diagnostics

- `GET /admin/mail` and `POST /admin/mail/test` require the existing `admin`
  permission. The POST accepts one explicit recipient and always uses the
  configured sender. It records `mail.test`, actor, provider, recipient count and
  success/failure in `AuditLog`, but not the body or address.
- `GET /debug/mail` is available only with `DEBUG=true`; otherwise it returns
  404. It reports boolean credential/configuration state but never secrets,
  tokens, raw provider responses or an environment dump.
- HTML mail is rendered with autoescaping Jinja templates in
  `app/templates/email/`. Never concatenate volunteer-controlled values into
  HTML.
- Graph retries only 429 and selected 5xx/network failures, honors bounded
  numeric `Retry-After`, and does not blindly retry permanent 4xx errors.

### 6. First deployment

1. Confirm the `development` GitHub Environment and its variables (below).
2. Confirm the Container App and migration job both reference the same
   `database-url` secret.
3. Optionally set GitHub Environment variable `SEED_DEMO_DATA=true` for a
   disposable DEV database only. The controlled database deployment job runs
   Alembic first and then the idempotent seed in the same execution; the web
   container never migrates or seeds on startup.
4. Push or merge the completed change to `dev`.
5. CI runs Black, Ruff, pytest, SQLite migration checks, a disposable PostgreSQL
   16 migration test, Bicep compilation and PowerShell parse validation.
6. The `dev` push workflow calls the reusable DEV deployment only after its
   test, PostgreSQL migration and infrastructure jobs all succeed. It then
   builds/pushes the exact tested commit image, deploys the revision, runs the
   same image as the database deployment job, waits for Alembic and the optional
   seed to succeed, and verifies the exact commit SHA, liveness and DB readiness.

Migration failure fails the GitHub deployment. It does not silently start a web
container that mutates its own schema.

## GitHub Environments and configuration

The bootstrap writes these non-sensitive values as GitHub Environment Variables,
not Secrets:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_CONTAINER_APP`
- `AZURE_CONTAINER_REGISTRY`
- `AZURE_CONTAINER_APP_ENVIRONMENT`
- `AZURE_MIGRATION_JOB`
- `AZURE_LOCATION`
- `APP_BASE_URL`

The workflow temporarily supports the old ST. PRIDE secret names as fallbacks so
existing OIDC is not broken during migration. Bootstrap moves non-sensitive
configuration to Variables without deleting legacy Secrets.

Environment behavior:

- `development`: `dev` deploys automatically after successful CI.
- `production`: reserved for `main`; configure Required Reviewers/manual approval
  before adding/enabling a production workflow.

This task does not deploy production and does not modify `main`.

## Database migrations and diagnostics

Alembic is the production schema authority:

```bash
DATABASE_URL="sqlite:///./volunteer.db" alembic upgrade head
DATABASE_URL="postgresql+psycopg://.../volunteer?sslmode=require" alembic upgrade head
```

Do not run `Base.metadata.create_all()` in production. It is used only in isolated
unit tests. Do not add `alembic upgrade head` to the application startup command.

- `/health/live` proves the process is serving without querying the database.
- `/health/ready` returns success only when the database is reachable.
- `/admin/db` requires the existing admin permission and renders reachability,
  expected/current Alembic revision and required-table status without returning
  HTTP 500 for an empty, pending or unavailable database.
- `/debug/db` returns the same safe structured result only when `DEBUG=true`;
  otherwise it returns 404.

Diagnostics never return `DATABASE_URL`, passwords, access tokens or raw driver
exceptions.

## Upgrading an existing SQLite deployment

Do not attach SQLite on Azure Files to the new production architecture and do not
delete the old database automatically.

1. Stop or quiesce writes to SQLite and take a verified backup.
2. Provision PostgreSQL without modifying the running Container App.
3. Deploy the PostgreSQL-capable image and configure the `database-url` secrets
   for both web app and migration job.
4. Run the Container Apps migration job and verify `/admin/db` or `/debug/db`.
5. If SQLite contains data that must survive, migrate it with a separate,
   rehearsed data-copy process; Alembic creates schema, not data transfer.
6. Compare record counts and critical workflows, then switch traffic.
7. Retain the SQLite backup until rollback and business validation are complete.
8. Only then decommission the SQLite mount; do not automatically delete Azure
   Files because it may later hold exports or uploaded files.

For the current ST. PRIDE DEV instance, the recent SQLite database contains
disposable fictional data, so data transfer is optional. PostgreSQL resource and
secret provisioning must still occur before the new DEV workflow is enabled.

## Networking and security notes

The default DEV template uses PostgreSQL public networking plus the Azure
services firewall rule (`0.0.0.0` to `0.0.0.0`), not an unrestricted Internet
range, and requires TLS through `sslmode=require`. This is the least disruptive
model for an existing Container Apps environment without VNet integration.

For production, prefer private networking when the Container Apps environment is
already VNet-integrated. Do not recreate a working environment merely to force
private networking into this milestone. The Container App and migration job have
system-assigned identities to prepare for future Entra PostgreSQL authentication;
password authentication remains the controlled first milestone.

## Troubleshooting

### OIDC subject mismatch

Check that GitHub uses the `development` Environment and that the federated
subject is exactly:

```text
repo:<owner>/<repository>:environment:development
```

Branch-subject credentials are not equivalent to Environment-subject credentials.

### Missing GitHub Environment or variables

Run `scripts/bootstrap.ps1` again. It is safe to repeat. Confirm your `gh` token
can administer repository environments and Actions variables.

### Missing Contributor rights

The OIDC service principal needs Contributor on the selected DEV Resource Group.
Subscription-wide Owner is neither required nor recommended.

### `roleAssignments/write` failure

Contributor cannot normally grant roles. Run only the exact RG-scoped command
printed by bootstrap using an identity with User Access Administrator or Owner,
then rerun bootstrap. The script never escalates itself.

### PostgreSQL connectivity

Verify the Flexible Server firewall/network choice, the database `volunteer`,
DNS resolution from Container Apps, and `sslmode=require`. Confirm both the web
app and migration job use a `DATABASE_URL` secret reference. Never print the
secret value in Actions logs.

### Pending or missing Alembic schema

Inspect `/admin/db`, then check the migration Job execution. Re-run the deployment
or manually start the job only after verifying it points at the intended database
and application image.

### Unhealthy Container App revision

The DEV workflow reports bounded revision state, filtered startup logs and the
migration execution status. `/healthz` must return the exact deployed commit SHA;
an HTTP 200 from an older revision is not considered success.

### EasyAuth failures

Confirm the Container App authentication provider, issuer/audience, redirect
URI, `AUTH_MODE=easyauth`, and admin email/group allowlists. `/debug/easyauth`
is available only with `DEBUG=true` and does not expose access tokens.

## Quality gates

Run before integration:

```bash
black .
black --check .
ruff check .
pytest
```

Regular tests require no Azure account, secrets, `/data` access or production
PostgreSQL. PostgreSQL-specific migration semantics are tested in GitHub Actions
with a disposable PostgreSQL 16 service container.

## Repository layout

```text
app/                    FastAPI application, auth, models and services
migrations/             Alembic schema history
infra/                  Bicep source, modules, parameters and compiled ARM JSON
scripts/bootstrap.ps1   Idempotent Azure/GitHub/OIDC bootstrap
scripts/start.sh        Deterministic web-only container entrypoint
.github/workflows/      CI and post-CI DEV deployment
tests/                  SQLite unit and PostgreSQL integration tests
```

See [DECISIONS.md](DECISIONS.md) for architecture decisions and
[DEMO.md](DEMO.md) for a product walkthrough.
