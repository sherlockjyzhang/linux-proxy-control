# Contributing

1. Create a branch and keep changes focused.
2. Never commit real addresses, hostnames, passwords, API tokens, Mihomo secrets, env files, databases, or runtime data.
3. Run the checks below before opening a pull request:

```bash
python3 -m compileall backend
pytest -q
```

The frontend is static and has no npm build step. Do not require Mihomo or a production server for tests. Describe behavior changes, security impact, and any deployment assumptions in the pull request.
