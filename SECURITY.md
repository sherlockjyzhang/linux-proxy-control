# Security Policy

## Supported versions

Only the latest default branch is currently supported.

## Reporting a vulnerability

Do not open a public issue for credentials, private addresses, or an exploitable vulnerability. Prefer GitHub Private Vulnerability Reporting or a GitHub Security Advisory at [this repository's advisory page](https://github.com/sherlockjyzhang/linux-proxy-control/security/advisories/new). If private reporting is unavailable, contact the maintainer through the GitHub account that owns this repository and ask for a private channel; do not publish the details in an issue. Include a minimal reproduction, affected version, impact, and a safe contact method.

This application has no built-in browser authentication or TLS. Deploy it only on a trusted LAN or behind an independently authenticated and TLS-terminating reverse proxy. Rotate any secret that may have been exposed before reporting.
