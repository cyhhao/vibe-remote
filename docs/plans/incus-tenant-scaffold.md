# Incus tenant scaffold

## Background

Vibe Remote can become a small hosted bot environment by running one isolated
tenant environment per user. Incus is a good first runtime because it supports
system containers and VMs, projects, profiles, cloud-init, and per-instance
resource limits.

## Goal

Add a repository-local scaffold that lets an operator create and manage multiple
Vibe Remote tenants on one Incus host without building the full avibe.bot SaaS
control plane yet.

## Design

- One Incus project per tenant: `vr-<tenant>`.
- One tenant instance per project: `vibe`.
- Per-tenant limits live on both the project and the default profile.
- Cloud-init installs Vibe Remote for a non-root `vibey` user and creates a
  systemd unit that starts/stops the local `vibe` runtime.
- Optional host port proxy exposes the tenant Web UI for local setup.
- The script stays standalone Python with no dependencies beyond the Incus CLI.

## Scope

- Add `scripts/incus_tenant.py`.
- Add operator docs in English and Chinese.
- Add focused tests for naming, cloud-init rendering, command planning, and CLI
  parsing behavior.

## Non-goals

- No avibe.bot backend integration yet.
- No automatic payment/provisioning flow.
- No promise that containers are the final security boundary for arbitrary
  untrusted paid users; the script supports VM mode for stricter isolation.
