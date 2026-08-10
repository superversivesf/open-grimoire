#!/bin/sh
# Generate a random SESSION_SECRET if the operator did not set one.
# app/config.py rejects short/placeholder secrets, so this only fills in
# when SESSION_SECRET is unset or empty — operators can still pin a secret.
if [ -z "$SESSION_SECRET" ]; then
  SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  export SESSION_SECRET
fi

exec "$@"
