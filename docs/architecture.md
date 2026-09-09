# Architecture and boundaries

## Request and transaction flow

```text
Edge TLS / proxy limits
  -> CORS / request ID / body bound / Redis IP limit
  -> trusted host / API validation
  -> verified JWT + Redis user limit + live DB session/role check
  -> owner-scoped domain operation
  -> domain changes + audit/outbox commit
  -> validated response
```

SQLAlchemy sessions are per request. Dependencies close/roll back sessions, but do not secretly commit after a response. Mutation services explicitly commit before returning. PostgreSQL is required; substituting SQLite would invalidate locking and concurrency guarantees.

Passwords use Argon2id off the event loop. Auth operations lock the user before token/session rows so password changes, rotations and logout serialize consistently. A JWT is not purely stateless: every protected request checks a live session and current role/auth version. This intentionally trades a DB lookup for immediate revocation.

Notes reads/writes always include the owner filter. PATCH and DELETE lock the row and require the version returned by the last read. The audit event commits with the mutation. UUIDs are identifiers, not permissions.

## Domain organization

`modules/auth` separates HTTP schemas/routes from reusable transactional behavior. `modules/notes` keeps its small queries near the endpoint; extract `service.py` and `repository.py` when workflows become shared or involve several aggregates. Keep dependency injection at the HTTP boundary and pass concrete sessions/context into services.

`models.py` currently provides one discoverable metadata registry for six small entities. When entities grow, move models into their owning modules and explicitly import all mapped classes from a metadata aggregation module used by Alembic. Avoid cyclic route imports and hidden model discovery.

For a new module:

1. Define strict input/output schemas; never expose ORM objects without a response model.
2. Add models and reviewed Alembic migrations. PostgreSQL indexes/constraints enforce invariants.
3. Define ownership/permission checks before loading mutable data.
4. Put multi-step transactions in a service; commit state and durable delivery intent together.
5. Include the router in the application factory.
6. Add success, denial, malformed-input, rollback and concurrency tests.

## Durable email

```text
API transaction -> password reset hash + encrypted email outbox
Beat every 10s -> Celery delivery task
Worker transaction -> due outbox row FOR UPDATE SKIP LOCKED
  -> SMTP accepted -> sent timestamp + erase encrypted payload -> commit
  -> failure -> attempts + next retry timestamp -> commit
```

The database outbox is the source of truth. A broker outage does not lose committed reset requests; a later scan finds them. SMTP is outside the database transaction boundary, so delivery remains at least once. The worker holds one short row lock while sending, with a 10-second network timeout. Expired reset links remain invalid even if email is retried.

Email recipient addresses are plaintext operational data; reset payloads are Fernet encrypted until delivery. Protect backups, SMTP and Mailpit access. Retention is a project policy: periodically archive/delete delivered outbox rows, expired token rows and audit events only after choosing retention requirements. Preserve refresh-reuse evidence for the lifetime of a still-active family.

## Scaling choices

Start with a modular monolith. Scale the API and Celery workers independently when measurements justify it. One Gunicorn process group exposes one private metrics listener on port 9000 with all its workers aggregated. Prometheus must discover each API instance when moving beyond the included single-instance Compose deployment.

Database connection budget is approximately API replicas × workers × (pool size + overflow), plus Celery child pools, migrations and operational headroom. The default API budget is 20 connections. Tune Argon2 concurrency/memory and endpoint rate caps with load tests on the target hardware.

Redis DB 0 is the broker, DB 1 holds API limits and DB 15 is testing-only. Logical DBs are not security boundaries. For production isolate broker and security state in separate authenticated/TLS Redis deployments and reserve memory; `noeviction` intentionally fails requests instead of silently dropping limits.

No multi-tenancy, SSO/OIDC, MFA, email verification, payment processing, full-text search, Kafka, Kubernetes manifests or arbitrary file-upload API is implied. Add these around actual requirements. The S3 adapter only builds private signed policies; wire it behind authorization and add object verification/malware quarantine before serving untrusted uploads.

## Upstream references

- [FastAPI deployment in containers](https://fastapi.tiangolo.com/deployment/docker/)
- [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Prometheus Python multiprocess limitations and lifecycle](https://prometheus.github.io/client_python/multiprocess/)
- [Python SMTP TLS support](https://docs.python.org/3/library/smtplib.html#smtplib.SMTP.starttls)
