# Contributing

## Development

1. Create a focused branch from `main`.
2. Keep credentials and personal data outside the repository.
3. Add tests for behavioral changes.
4. Run `python scripts/release_check.py`.
5. Open a pull request using the repository template.

Use Node.js `20.19+`, `22.13+`, or `24+` for the frontend. Docker Compose remains the preferred reproducible local runtime.

## Security And Privacy

Do not commit `.env` files, OAuth credentials, Gmail tokens, private keys, database exports, Chroma data, personal PDFs, notes, email content, backups, or unredacted logs. Use `.env.example` for publishable configuration names and synthetic values in tests.

Report vulnerabilities through the process in `SECURITY.md`, not through a public issue.

## Pull Requests

Keep changes scoped, explain operational impact, and preserve existing release, migration, security, and API-contract policies. Dependency or vulnerability exceptions must be exact, temporary, owned, and documented with compensating controls.
