# FastAPI Boilerplate

A modular Python 3.13 / FastAPI foundation for a growing backend. Async PostgreSQL, secure session-based JWT authentication, Redis rate limiting, reliable email delivery and Docker observability are wired together and tested—not just listed as dependencies.

This is a production-oriented starting point. Deployment-specific HTTPS, infrastructure hardening, SSO/MFA, data retention and load testing still require deliberate configuration.

## Quick start

Requires Docker Engine/Desktop with Compose v2 and Python 3 for secret generation.

```bash
git clone https://github.com/pyaidev/fastapi-boilerplate.git
cd fastapi-boilerplate
make init
make up
make test
```

`make init` creates an ignored, permission-restricted `.env` with independent random signing, encryption, database and Grafana credentials. It never replaces an existing file. Do not use placeholder secrets from `.env.example`.

| Service | Local address |
| --- | --- |
| API / Swagger | http://localhost:8002/docs |
| Readiness | http://localhost:8002/health/ready |
| Grafana | http://localhost:3001 |
| Prometheus | http://localhost:9091 |
| Mailpit development inbox | http://localhost:8026 |
| PostgreSQL / Redis | 127.0.0.1:55432 / 127.0.0.1:56379 |

Grafana's username is `admin`; its generated password is in your local `.env`. A datasource and dashboard are provisioned automatically. Every published port is loopback-only. The database and Redis use Docker named volumes; normal `make down` retains them.

All configuration is baked into images to work on Docker Desktop without host bind mounts. Rebuild after code/config changes.

## Included

- FastAPI application factory and lifespan-managed connection pools; versioned `/api/v1` routes.
- SQLAlchemy 2 async PostgreSQL, explicit transaction boundaries, Alembic migrations and migration drift checks.
- Argon2id passwords, short-lived access JWTs, opaque hashed refresh tokens, rotation/reuse detection and immediate session revocation.
- Database-backed user/admin authorization; no client-controlled privilege fields.
- Password change/reset; single-use reset tokens; encrypted transactional email outbox delivered by Celery worker + Beat.
- Owner-scoped Notes CRUD example, bounded cursor pagination, search/filtering and optimistic version conflicts under concurrent writes.
- Atomic Redis limits shared across workers; explicit proxy trust, body-size bounds, safe validation errors and request IDs.
- JSON application logs, private multiprocess Prometheus metrics, alert rules and Grafana dashboard.
- Optional private S3 presigned-upload adapter and production Sentry integration.
- Non-root multi-stage Docker images, hashed dependency exports, health checks and separate migration job.
- Real PostgreSQL/Redis integration tests, Ruff, mypy, coverage gate, pre-commit and GitHub Actions.

## Project layout

```text
app/
  main.py                 # Factory, lifecycle, middleware and route composition
  cli.py                  # Explicit operator commands
  models.py               # Typed relational schema and shared metadata
  api/dependencies.py     # DB sessions, identity and permission dependencies
  core/                   # Settings, security, errors, logging, rates, middleware
  infrastructure/         # Database, Celery outbox, optional S3 adapter
  modules/
    auth/                 # Schemas, HTTP routes and transactional auth service
    users/                # Admin user list and current-user audit feed
    notes/                # Ownership-safe CRUD example
  static/                 # Small functional password-reset page
migrations/               # Reviewed Alembic history
monitoring/               # Prometheus rules and provisioned Grafana dashboard
scripts/                  # Secret generation, isolated test runner and backup
tests/                    # Unit, API, security and concurrency checks
docs/                     # Architecture and operational decisions
```

Domain modules own their HTTP contracts and behavior; infrastructure stays outside them. Add service/repository layers when a module actually needs reusable transaction/query logic. Do not create a generic CRUD abstraction that hides ownership filters. See [architecture](docs/architecture.md).

## API usage

Register, then request an OAuth2-compatible password-form token. These commands contain example credentials only.

```bash
curl -X POST http://localhost:8002/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","name":"Example","password":"example-long-password-123!"}'

curl -X POST http://localhost:8002/api/v1/auth/token \
  -d 'username=you@example.com&password=example-long-password-123!'
```

Swagger's **Authorize** uses the same email/password form. Send access tokens in `Authorization: Bearer <access_token>`. Never put them in URLs.

| Route | Behavior |
| --- | --- |
| `POST /api/v1/auth/register` | Register a normal user |
| `POST /api/v1/auth/token` | Login; returns access + refresh tokens |
| `POST /api/v1/auth/refresh` | Rotate with JSON `refresh_token` |
| `POST /api/v1/auth/logout` | Revoke the current session family |
| `GET /api/v1/auth/me` | Current user |
| `POST /api/v1/auth/password` | Change password; revoke all sessions |
| `POST /api/v1/auth/password-reset` | Generic response; email if account exists |
| `POST /api/v1/auth/password-reset/confirm` | Consume reset token |
| `GET/POST /api/v1/notes` | List/create owned notes |
| `GET/PATCH/DELETE /api/v1/notes/{id}` | Read/update/delete owned note |
| `GET /api/v1/audit` | Current user's audit events |
| `GET /api/v1/admin/users` | Administrator-only user list |

Refresh tokens are strictly single-use. Serialize refresh requests on the client and replace both tokens atomically. Replaying a used token revokes the entire family, including the replacement; the previous access token also stops working after rotation. If a refresh response is lost, log in again. Each rotation extends the refresh expiry; add an absolute family lifetime if your policy needs it.

Notes PATCH requires JSON `expected_version`; DELETE requires the query parameter. Stale writes return 409. A note belonging to another user returns 404. Listing returns `{"items": [...], "next_cursor": "..."}`; pass the cursor back with unchanged filters.

API validation/domain errors contain `error.code`, `message`, `details`, and `request_id`. Secrets and submitted validation input are not echoed.

## Rate limits

| Scope | Default |
| --- | --- |
| API IP | 1,000/minute |
| Authenticated user | 600/minute |
| Login | 5/minute/IP |
| Register | 5/hour/IP |
| Reset request + confirm | Shared 5/hour/IP |
| Refresh | 30/minute/IP |

Limits are fixed windows starting at the first request, implemented with one atomic Redis script. 429 includes `Retry-After`; Redis failure returns 503 for limited requests. Health checks remain available. Distributed abuse/DDoS protection belongs at the edge as well.

Forwarded headers are ignored by default. Only an exact peer in `TRUSTED_PROXY_IPS` may supply a valid `X-Real-IP`. Configure the reverse proxy to overwrite that header; never trust arbitrary internet-provided forwarding headers.

## Operator commands

```bash
docker compose exec api python -m app.cli create-admin --email admin@example.com
docker compose exec api python -m app.cli outbox-status
docker compose exec api python -m app.cli retry-email <dead-letter-uuid>
make backup
```

Admin creation prompts privately for the password and refuses to promote/overwrite existing accounts. Password-reset links open `/reset-password`; the browser removes their token fragment from the address bar and submits it as JSON. Local mail goes only to Mailpit.

SMTP delivery is at least once: a crash after SMTP accepts mail but before database commit can produce a duplicate. The stable Message-ID helps correlation; it is not a guarantee of recipient deduplication. Failed messages back off and stop after 10 attempts. Keep one Beat scheduler; multiple workers safely claim rows with `SKIP LOCKED`.

## Development and verification

```bash
uv sync --frozen
make check
make unit
make test-local
uv run --frozen python -m scripts.test --local --cov=app --cov-report=term-missing
uv run --frozen pre-commit install
```

`make test` runs in Docker. `make test-local` uses the Compose database/Redis ports. Both create a random isolated `fastapi_test_*` database, migrate it, check schema drift, run tests, test downgrade/upgrade and remove only that database. Redis database 15 is reserved for tests and is flushed; never point the test runner at production or run two suites against the same Redis instance.

For an IDE-hosted API, provide local `DATABASE_URL` and `REDIS_URL` overrides (preserving generated credentials), then run `uv run uvicorn app.main:create_app --factory --reload --port 8002 --no-proxy-headers`. Stop the Compose API first to free the port. Multiprocess metrics are served by Gunicorn, not this development command.

After dependency changes run `make lock`, review and commit both `uv.lock` and `requirements/`. CI checks lint/types, 80% aggregate coverage, real services, migration round-trip, image build and Prometheus configuration. CI does not deploy automatically.

## Production

Read [operations and backup/restore](docs/operations.md), [architecture](docs/architecture.md) and [security policy](SECURITY.md) before deployment. The default Compose stack is a single-host development/staging topology, not high availability.

Static password-reset assets are served by Starlette. WhiteNoise is a WSGI tool and is not used here; larger static bundles should be hosted by the reverse proxy/CDN. Uploaded media belongs in private object storage, not the source tree.
