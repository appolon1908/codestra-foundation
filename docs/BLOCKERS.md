# Blockers and External Gates

## Local Docker Hub authentication

- Date: 2026-08-30
- Class: environmental
- Action: build `python:3.12.11-slim` container locally
- Result: Docker Hub token request returned `401 Unauthorized: incorrect username or password`
- Impact: local container build could not start; no source compilation step failed
- Mitigation: GitHub CI has an independent clean-runner container-build job
- Resolution owner: server Docker credential administrator
- Source-gate result: clean GitHub container build passed for the v1 completion
  source in run `33324025094`; only this host's Docker credential repair remains

This does not authorize substituting an unpinned or unreviewed base image. The
GitHub build result is required before the source gate is considered green.

## Production activation

Deployment, Keycloak/Kong provisioning, Middleware live canary, and Klyrow data
migration remain external owner-approved gates. No production flag is enabled.
