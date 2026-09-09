# Operations

## Before exposing a service

- Set `APP_ENV=production`, `DEBUG=false`, `DOCS_ENABLED=false`; use a real HTTPS `PUBLIC_BASE_URL`. Settings reject unsafe production values.
- Generate independent secrets per environment. Use a secret manager at deployment; do not bake `.env` into images. Do not log resolved Compose configuration because it includes credentials.
- Put the API behind TLS termination with request-body and slow-client timeouts. Keep ports 9000, PostgreSQL, Redis, Mailpit, Prometheus and Grafana private. Compose publishes only to loopback, which is still accessible to local users.
- Set explicit `ALLOWED_HOSTS` and `CORS_ORIGINS`. Include `127.0.0.1` for the provided internal health probe. Trust only exact proxy peers and have the proxy overwrite `X-Real-IP`. Gunicorn's Uvicorn worker deliberately disables automatic forwarded-header interpretation.
- Set a real SMTP provider, usually port 587, `EMAIL_USE_TLS=true`, username/password/from address. STARTTLS uses a certificate-verifying SSL context. The shipped Mailpit dependency is development-only; replace/remove it in your production Compose override.
- Use least-privileged runtime database and Redis accounts. The local database owner can create databases to support the test runner; production runtime should not have that privilege. Use a separate migration credential/job.
- Add database/Redis TLS, encrypted disks and independently tested, off-host encrypted backups. Managed database failover and Redis durability/HA are infrastructure decisions.
- Keep exactly one Beat scheduler. Monitor worker health, dead letters and oldest pending email; `python -m app.cli outbox-status` provides a local diagnostic.
- Set resource limits at your orchestrator; load-test CPU, Argon2 memory, connection budget, Redis memory and p95 response time. Do not infer a requests-per-second promise from functional tests.
- Choose audit/token/outbox retention and privacy policies. Do not claim the mutable application audit table is a tamper-proof compliance ledger.
- Lock down Grafana with TLS/access control and rotate its initial admin password. Changing `.env` does not reset a password already persisted in the Grafana volume.
- Configure Alertmanager/notification receivers for actionable alerts. Included Prometheus rules alone do not send notifications.
- Browser integrations should use a secure backend-for-frontend or another reviewed token strategy; no refresh tokens in localStorage. If switching to cookies, implement Secure/HttpOnly/SameSite and CSRF protection. The included API uses explicit bearer headers, not ambient cookies.

## Deployment and migrations

The `migrate` service completes `alembic upgrade head` before the API/worker start. Do not run migrations inside every API worker. For rolling deployments use backward-compatible expand/contract migrations and a separately coordinated migration job; Compose ordering is not a zero-downtime strategy.

```bash
docker compose build api
docker compose run --rm migrate
docker compose up -d --no-build --wait
docker compose exec api alembic current
docker compose exec api alembic check
```

Generate migrations locally against a development database with `uv run alembic revision --autogenerate -m "describe change"`; review every change, especially drops/renames and data backfills. CI tests a fresh upgrade, drift and an isolated downgrade/upgrade. Never use destructive downgrade as an unreviewed production rollback.

`make down` retains data volumes. Do not use `docker compose down -v` unless you intentionally want to erase all project data.

## Backup and restore rehearsal

`make backup` writes a new permission-restricted `backups/postgres-*/database.dump` in PostgreSQL custom format. It never overwrites an older backup and leaves a failed partial file clearly marked by its error output. A dump is not a backup strategy until encrypted/off-host copies and restore drills are automated.

To rehearse a restore, use a brand-new database name and your explicit backup path. The example target must not already exist; no existing database is dropped or overwritten:

```bash
docker compose exec -T db createdb -U fastapi fastapi_restore_drill
docker compose exec -T db pg_restore -U fastapi -d fastapi_restore_drill --no-owner --no-acl --exit-on-error < backups/postgres-EXAMPLE/database.dump
docker compose exec -T db psql -U fastapi -d fastapi_restore_drill -c 'SELECT version_num FROM alembic_version;'
```

These examples use default `POSTGRES_USER=fastapi`; substitute your configured user. Validate table counts, constraints and a read-only application check before any planned recovery. Leave the drill database until reviewed; remove only that exact database when you intentionally clean up. Production recovery needs a cutover plan, compatible code, known RPO/RTO and verified key availability.

Back up encryption/signing keys separately in a secret manager. Changing `OUTBOX_ENCRYPTION_KEY` without re-encrypting unsent payloads makes queued email unreadable. A signing-key rotation invalidates access tokens but does not itself revoke stored refresh tokens; revoke sessions as part of incident response when needed.

## Monitoring and incident checks

```bash
docker compose ps
docker compose logs --tail=100 api worker beat
docker compose exec api python -m app.cli outbox-status
docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml
```

`/health/live` checks process liveness. `/health/ready` verifies PostgreSQL and Redis and returns 503 when a dependency fails. Public `/metrics` is not exposed; Gunicorn serves the aggregated metrics only on internal port 9000.

The included dashboard shows availability, request rate/status, p95 latency, in-flight work and rate-limit rejections. Labels use route templates, not arbitrary URL paths or user IDs. Prometheus stores up to 15 days/2 GB. Fresh installations need traffic and a few scrape intervals for rate panels.

API logs include request IDs and error types, not payloads, tokens or bound SQL parameters. If enabling Sentry, configure its project-side scrubbing/retention and review event payloads before using real data; `send_default_pii=false` is not a complete data-loss prevention policy.

## Optional integrations

S3 uses the standard boto3 credential chain (prefer an IAM role over long-lived keys), `S3_BUCKET`, `S3_REGION` and optional endpoint URL. Keep the bucket private; apply least-privilege prefix permissions, CORS and lifecycle rules. Presigned POST policies allow PNG/JPEG/PDF up to 10 MiB for 5 minutes. MIME metadata is not proof of safe content; verify uploaded object size/type and scan/quarantine as your use case requires. The adapter is tested with a stub, not a real cloud bucket.

Set `SENTRY_DSN` to enable production error reporting. Real S3, SMTP-provider and Sentry behavior require credentials and are not exercised by offline CI. Local SMTP is verified using Mailpit.

Celery outbox retries stop after 10 attempts. Investigate SMTP/configuration first, list dead-letter IDs with `outbox-status`, then explicitly retry one with `retry-email UUID`. Replaying cannot extend an expired password-reset token.
