# Architecture decisions

## Azure Easy Auth remains the authentication boundary

The application does not implement another login flow. Azure Container Apps
Easy Auth authenticates requests; the application maps trusted Easy Auth
identity headers to permissions. `AUTH_MODE=disabled` exists only for local and
temporary DEV work and must not be used for shared or production operation.

## SQLite is a single-replica prototype database

SQLite keeps the prototype inexpensive and portable. Foreign keys and a busy
timeout are enabled, migrations run before Uvicorn, and only transient lock
errors are retried. Azure Container Apps must use one active revision and
exactly one replica. PostgreSQL is the preferred next-phase database.

## Public editing uses random bearer-style tokens

Public registrations receive a cryptographically random edit token. Only its
SHA-256 hash is stored. The token can be revoked and possession is required to
read or change the registration. Looking up an e-mail address never exposes a
different person's data.

## Messages remain in a persistent outbox

The prototype creates confirmation, change and cancellation previews without
sending external e-mail or SMS. A future provider should consume the outbox
through an explicit delivery state machine.

## Historical assignments are retained

Assignments are status-driven instead of hard-deleted. Anonymization removes
personal data and edit access irreversibly while preserving staffing history.
Relevant administrative changes are recorded in the audit log.

## Open points for the next phase

- Replace SQLite/Azure Files with managed PostgreSQL before multi-replica use.
- Add a production mail provider with retries and delivery status.
- Add finer-grained Entra groups and automated data retention.
- Add dedicated CSRF tokens if the app is exposed without the Easy Auth
  same-site authentication boundary.
