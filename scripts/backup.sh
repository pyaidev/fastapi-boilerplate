#!/bin/sh
# Run from the repository root. Does not remove or replace previous backups.
set -eu
umask 077
mkdir -p backups
backup_dir=$(mktemp -d "./backups/postgres-XXXXXXXX")
if docker compose exec -T db sh -c 'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$backup_dir/database.dump"; then
    printf 'Backup created: %s/database.dump\n' "$backup_dir"
else
    printf 'Backup failed; partial archive retained at %s/database.dump (do not restore it)\n' "$backup_dir" >&2
    exit 1
fi
