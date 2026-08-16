# Architecture decisions

## Azure Easy Auth remains the authentication boundary

The application does not implement another login flow. Azure Container Apps
Easy Auth authenticates requests; the application maps trusted Easy Auth
identity headers to permissions. `AUTH_MODE=disabled` exists only for local and
temporary DEV work and must not be used for shared or production operation.

## PostgreSQL is the shared Azure database

Azure deployments use PostgreSQL Flexible Server through `DATABASE_URL` and
psycopg 3. SQLite remains a lightweight local/test option only. Azure Files is
not a supported database transport; existing file shares are retained for
possible future exports/uploads and are not deleted automatically.

Alembic is the production schema authority. The web image starts deterministically
without migrating or seeding. A manual Container Apps Job runs the same image and
must finish `alembic upgrade head` before deployment health verification.

Password authentication with TLS is the first PostgreSQL milestone. Web and job
resources already have managed identities so Entra database authentication can
replace the password later without redesigning the application boundary.

## Public editing uses random bearer-style tokens

Public registrations receive a cryptographically random edit token. Only its
SHA-256 hash is stored. The token can be revoked and possession is required to
read or change the registration. Looking up an e-mail address never exposes a
different person's data.

## Messages use a persistent outbox

Application workflows create messages in the outbox according to administrable
mail templates and delivery modes. SMTP delivery is explicit and failure-tolerant;
the SMTP password remains an environment/Azure secret rather than database data.

New transactional delivery code uses the provider-neutral `MailService` boundary
with `console` and Microsoft Graph providers. Graph uses app-only authentication
and the configured shared mailbox; business routes must never call Graph
directly. The existing SMTP/outbox workflow remains isolated until a later
workflow migration, and this foundation deliberately adds no queue worker.

For Microsoft 365, Exchange Online Application RBAC is the authorization source
of truth. A scoped `Application Mail.Send` role replaces—not supplements—an
unscoped Entra `Mail.Send` grant, because permissions from both authorities are
additive. Client secrets are supported initially; managed identity is the future
credential model behind the existing token-provider boundary.

## Historical assignments are retained

Assignments are status-driven instead of hard-deleted. Anonymization removes
personal data and edit access irreversibly while preserving staffing history.
Relevant administrative changes are recorded in the audit log.

## Open points for the next phase

- Replace PostgreSQL password authentication with managed identity/Entra auth
  after operational validation.
- Insert the persistent outbox between business workflows and `MailService`.
- Add finer-grained Entra groups and automated data retention.
- Add dedicated CSRF tokens if the app is exposed without the Easy Auth
  same-site authentication boundary.
