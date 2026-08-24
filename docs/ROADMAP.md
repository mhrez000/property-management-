# Build Roadmap

Staged sequencing from the Technical Build Blueprint, riskiest-first. The app
must stay deployable at every stage (trunk-based; feature-flag incomplete work).

## Phase 0 — De-risking prototypes (this repo's starting point)

1. **Trust-accounting ledger** — immutable `journal_entry`/`posting`, DB
   balancing + immutability triggers, integer cents, gap-free receipt sequence,
   three-way reconciliation. Threshold to proceed: reconciliation ties to the
   cent across randomised transaction sets; cross-tenant tests pass. ✅
2. **Payments receipting path** — provider-agnostic interface with a sandbox
   provider proving: payment reference per tenancy → webhook → idempotent
   ledger receipt. Swap in Monoova (first choice; Zepto/Azupay alternates) once
   sandbox credentials and legal sign-off exist. ✅ (sandbox)
3. **Legal** — engage AU financial-services counsel on operating under the
   provider's AFSL vs needing our own, and AML/CTF exposure. ⛔ Blocking for
   live payments; not blocking for build.

## Phase 1 — Foundation (months 2–5)

- Auth / Organisation / RBAC, RLS from commit one ✅
- Property / Owner / Tenant / Tenancy / Lease core ✅
- Rent schedules feeding the ledger ✅
- Celery + Redis; GitHub Actions with migration-safety + tenant-isolation tests ✅

## Phase 2 — Product (months 5–10)

- Arrears automation: trigger–condition–action engine over a transactional
  outbox; human-in-the-loop for notices (never auto-issue a termination).
- Owner/tenant/contractor portals as a Next.js PWA (`apps/web`).
- Xero sync (batched, rate-limit- and egress-aware), Annature e-signing.
- Compliance rules engine surfaces: bond lodgement workflows, rent-increase
  validation, audit-pack outputs per state.

## Phase 3 — Differentiators (months 10+)

- Native Expo inspection app, offline-first with background photo upload.
- Bricks+Agent maintenance integration (open API); Tapi via partnership.
- AI: comms drafting + invoice extraction via Claude on Bedrock (`au.*` Geo
  profiles, ZDR), always draft-and-approve.

## Standing constraints

- **No tenant fees, ever** — provider transaction fees are absorbed into
  agency plan pricing.
- Money is integer cents end-to-end; corrections are reversing entries.
- Every tenant-scoped table carries `organisation_id` under RLS.
- State rules are effective-dated data, not code.
- AI output is always a draft with an audit trail; a human approves anything
  tenant-affecting (Privacy Act ADM transparency obligation, 10 Dec 2026).
