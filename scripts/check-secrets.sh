#!/usr/bin/env bash
# Pre-commit guard for a public repo: refuse to commit .env files or token-looking strings.
# Install once per clone:  ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-commit
set -euo pipefail

staged=$(git diff --cached --name-only --diff-filter=ACM)
[ -z "$staged" ] && exit 0

if echo "$staged" | grep -Eq '(^|/)\.env(\..+)?$' && ! echo "$staged" | grep -Eq '(^|/)\.env\.example$'; then
  echo "check-secrets: refusing to commit a .env file" >&2
  exit 1
fi

# JWTs (GFW tokens), and assignments of the project's secret variables to a value.
pattern='eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|(GFW_API_TOKEN|FEATHERLESS_API_KEY)=[^[:space:]]+'
if git diff --cached -U0 -- . ':(exclude)scripts/check-secrets.sh' | grep -E '^\+' | grep -Eq "$pattern"; then
  echo "check-secrets: staged changes look like they contain a secret; aborting" >&2
  exit 1
fi
