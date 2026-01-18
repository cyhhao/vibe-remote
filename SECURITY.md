# Security Policy

## Reporting a Vulnerability
- Please open a private issue or email the maintainer (add contact) with details.
- Do not disclose publicly until we confirm a fix or mitigation.

## Secrets
- Never commit secrets. Store them in `~/.vibe_remote/config/config.json` or a secret manager.
- Tokens required: Slack tokens, and the Claude/Anthropic credentials used by `claude-code-sdk` (e.g., `ANTHROPIC_API_KEY`).
