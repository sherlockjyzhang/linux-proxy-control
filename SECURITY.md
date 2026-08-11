# Security Policy

## Supported versions

Only the latest default branch is currently supported.

## Reporting a vulnerability

Do not open a public issue for credentials, private addresses, or an exploitable vulnerability. Prefer GitHub Private Vulnerability Reporting or a GitHub Security Advisory. If those features are not enabled, email the maintainer at `security@example.com` (replace this address with the project maintainer's real security mailbox before publishing) and include a minimal reproduction, affected version, impact, and a safe contact method.

This application has no built-in browser authentication or TLS. Deploy it only on a trusted LAN or behind an independently authenticated and TLS-terminating reverse proxy. Rotate any secret that may have been exposed before reporting.
