# Security Policy

## Supported version

Security fixes are applied to the latest commit on the default branch. Older
releases should be upgraded before a report is evaluated.

## Reporting a vulnerability

Please use GitHub's private security-advisory reporting flow for this
repository. Do not open a public issue containing an exploit, malicious sample,
token, host path, or other sensitive information.

Include the affected commit, deployment mode, reproduction steps, impact, and
any suggested mitigation. Maintainers should acknowledge a report within seven
days and coordinate disclosure after a fix is available.

## Deployment boundary

The public Web container does not receive the Docker socket. Docker access is
isolated in `pandocr-controller`, which exposes only model lifecycle actions on
an internal network. Keep all published ports bound to loopback unless an API
token, TLS reverse proxy, and explicit CORS origins are configured.
