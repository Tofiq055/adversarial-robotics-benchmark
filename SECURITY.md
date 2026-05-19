# Security Policy

## Scope

This repository is an **academic research tool** for evaluating LLM safety in robotic control scenarios. It is not production software and does not handle end-user data or authentication flows.

That said, security matters here because:

- The `.env` file may contain API keys (HuggingFace, NVIDIA NIM, OpenAI).
- The Docker setup mounts the host Docker socket (`/var/run/docker.sock`), which carries privilege implications.
- Fine-tuned model weights and adversarial datasets are intentionally withheld from the public repository.

## Reporting a Vulnerability

If you discover a security issue — leaked credentials in commit history, a container escape vector, or any way this framework could be weaponized beyond its intended simulation-only scope — please report it **privately**.

**Contact:** Open a [GitHub Security Advisory](https://github.com/tofiq055/adversarial-robotics-benchmark/security/advisories/new) or email the maintainer directly.

**Do not** open a public issue for security-sensitive findings.

## What We Consider In-Scope

| Category | In-Scope | Out-of-Scope |
|----------|----------|--------------|
| Credential leakage | Secrets in git history, Docker layers | User's own `.env` mismanagement |
| Container security | Escape from `a4_sim` sandbox to host | General Docker daemon hardening |
| Adversarial amplification | Modifications that remove safety guardrails from the published artifact | Research extensions within simulation |

## Supported Versions

Only the latest commit on the `main` branch is actively maintained.
