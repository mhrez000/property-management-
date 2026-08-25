# PropChief — Australian Property Management SaaS

A modern, AI-assisted, mobile-first, **trust-account-grade** property management
platform for the Australian market. Built to reduce property-manager burnout,
with a hard product rule: **tenants are never charged a fee to pay rent.**

This repository implements the architecture defined in the two founding
documents (see `docs/`):

- *Competitive Market Research and Build Strategy*
- *Technical Build Blueprint*

## Monorepo layout

```
/apps
  /api        Django 5 + DRF API, Celery workers (the core product)
  /web        Next.js web app + PWA portals (placeholder — built after API core)
  /mobile     Expo/React Native offline-first inspection app (placeholder)
/packages
  /shared-types  OpenAPI-generated TypeScript types (generated from the API schema)
/infra        IaC (Terraform/CDK) and deployment config (placeholder)
/docs         Architecture notes and the build roadmap
```

## Core architectural decisions (from the blueprint)

| Concern | Decision |
| --- | --- |
| Backend | Django 5 + Django REST Framework, Python 3.12, PostgreSQL 16 |
| Multi-tenancy | Shared schema, mandatory `organisation_id`, enforced by **PostgreSQL Row-Level Security** (not django-tenants) |
| Trust accounting | Built from scratch: immutable double-entry journal, integer cents, DB-level balancing + immutability triggers, gap-free statutory receipt sequence, three-way reconciliation |
| Payments | Licensed AU NPP provider (Monoova-style PayTo/PayID) behind a provider-agnostic interface; unique payment reference per tenancy; idempotent webhook receipting. No tenant fees, ever |
| Compliance | Effective-dated rules engine (`jurisdiction, rule_type, effective_from/to, config`) covering all 8 AU jurisdictions — reforms are data changes, not code changes |
| Background jobs | Celery + Redis (Temporal considered later for month-end disbursement) |
| Hosting target | AWS Sydney (ap-southeast-2); PaaS acceptable for MVP |
| AI | Claude on AWS Bedrock pinned to `au.*` Geo profiles; drafts only, human-in-the-loop |

## Quick start (local development)

Requirements: Docker + Docker Compose.

```bash
cp .env.example .env
docker compose up --build
# API available at http://localhost:8000/api/v1/  (OpenAPI docs at /api/schema/swagger-ui/)
```

Or natively (Python 3.11+, PostgreSQL 16, Redis):

```bash
cd apps/api
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
createuser propman --createdb && createdb propman -O propman   # role must NOT be superuser (RLS)
python manage.py migrate
python manage.py runserver
```

> **Why a non-superuser database role matters:** row-level security policies are
> bypassed by superusers. The application role must be a plain role; tables are
> created with `FORCE ROW LEVEL SECURITY` so policies also bind the table owner.

## Running tests

```bash
cd apps/api
pytest
```

The suite includes property-based (Hypothesis) tests for the ledger's money
math, DB-level tests that unbalanced/mutated journal entries are rejected by
Postgres itself, and explicit cross-tenant isolation tests asserting RLS blocks
access.

## Build sequencing

Riskiest-first, per the blueprint — see `docs/ROADMAP.md` for the full staged
plan. Current status:

- [x] Monorepo + Django foundation, Docker, CI
- [x] Auth / Organisation / RBAC / RLS tenant isolation
- [x] Property / Owner / Tenant / Tenancy / Lease / RentSchedule core
- [x] Trust-accounting ledger engine (double-entry, immutable, reconciliation)
- [x] Payments provider abstraction + webhook receipting (sandbox provider)
- [x] State compliance rules engine, seeded for all 8 jurisdictions
- [x] Arrears automation (TCA engine + transactional outbox)
- [x] Bond lodgement workflows (jurisdiction-driven, trust-integrated)
- [x] Owner allocation + month-end disbursement runs (approval-gated)
- [ ] Owner/tenant/contractor portals (Next.js PWA)
- [ ] Native inspection app (Expo, offline-first)
- [ ] Maintenance integrations (Bricks+Agent / Tapi)
- [ ] AI features (comms drafting, invoice extraction — Claude on Bedrock)

## Legal / compliance caveats

- Payments licensing (operating under a provider's AFSL) **requires Australian
  financial-services legal advice before launch** — do not enable live money
  movement until confirmed.
- State rules seeded in the compliance engine are sourced from the research
  documents; verify each figure against the relevant regulator before
  production use. They are data rows, designed to be corrected without code
  changes.
