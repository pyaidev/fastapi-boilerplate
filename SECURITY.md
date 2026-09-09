# Security

Do not include credentials, tokens, personal data or a working exploit against a deployed service in public issues.
Report a suspected vulnerability privately to the repository owner through GitHub private vulnerability reporting when enabled.

Before exposing this template, complete [the production checklist](docs/operations.md).
This repository is a tested starting point, not an audited identity provider, compliance certification or an availability guarantee.

Never commit `.env`, database dumps, real reset links, Telegram tokens or cloud credentials.
CI keys are intentionally public and test-only. Generate independent keys for each environment.
