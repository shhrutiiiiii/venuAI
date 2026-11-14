# Multi-Tenancy — VenuAI

## Purpose
Documents the Phase 4 tenant/organization isolation layer: how tenants are created, how every user and resource is scoped to one, and what "admin" means now that more than one organization can exist in the same deployment.

## Responsibilities
- Describe the tenant data model and how it's seeded for backward compatibility with every pre-Phase-4 record.
- Enumerate exactly which resource types are tenant-scoped today and which still are not.
- State the authorization rule precisely: tenant match is checked *before* the existing owner/classification/role rule, not instead of it.

## Scope
Covers tenant creation/lookup (`VENUAI/auth.py`), the tenant filter added to documents/datasets/multimodal documents/knowledge-graph entities and relationships (`VENUAI/rag_server.py`, `VENUAI/knowledge_graph/store.py`), and the `/auth/tenant` endpoint. Does not cover billing, per-tenant quotas, or tenant-level admin management UI — none of that exists.

## High-Level Overview
A **tenant** is an organization/workspace. Every user belongs to exactly one tenant (`users.tenant_id`), and every owned resource (document, dataset, multimodal document, knowledge-graph entity/relationship) now also carries a `tenant_id`. A request from a user in tenant A can never see a resource belonging to tenant B, regardless of role — **admin is a per-tenant role, not a cross-tenant superuser.**

Backward compatibility is additive, not a breaking migration: every user and resource created before this phase has no `tenant_id` value on disk, and is treated as belonging to a single implicit `"default"` tenant (seeded automatically in `init_db()`), both at write time (new columns default to `'default'`) and at read time (`info.get("tenant_id", "default")` for the JSON-file-backed stores that predate a schema). Existing single-tenant deployments are unaffected unless a second tenant is actually created.

```
POST /auth/register {email, password, organization_name?, tenant_id?}
   │
   ├─ organization_name set   → create_tenant(name) → new tenant_id, caller becomes its first user
   ├─ tenant_id set (no name) → get_tenant(tenant_id); 404 if it doesn't exist; caller joins it
   └─ neither set             → caller joins "default"
   ▼
users.tenant_id persisted; every subsequent JWT-authenticated request carries it via AuthenticatedUser.tenant_id
```

## Design Principles
1. **Tenant check comes first, and fails closed as 404.** Every access-control function (`_check_document_access`, `_check_dataset_access`, the knowledge-graph access clauses) checks `tenant_id` before checking owner/classification/role. A cross-tenant resource returns 404 ("not found"), not 403 ("forbidden") — this avoids confirming a cross-tenant resource's existence to a caller who isn't in that tenant.
2. **Admin is scoped to one tenant.** Before this phase, `role == "admin"` meant "see everything in the system." That was only ever correct because the system had one implicit tenant. Phase 4 keeps "admin sees everything" literally true, but "everything" now means "everything in admin's own tenant" — see `get_visible_doc_ids()` and `VENUAI/tests/test_retrieval_authorization.py::test_get_visible_doc_ids_admin_sees_everything_in_their_tenant`.
3. **Deterministic cross-document entity IDs must include the tenant.** `Entity.make_id()` hashes `(entity_type, name)`. Without the tenant in that hash, two different tenants both mentioning "LRDE" would resolve to the *same* graph node — a real cross-tenant data leak, not just a filtering gap, since `add_entity()` is an upsert keyed by `entity_id`. `make_id()` therefore hashes `(tenant_id, entity_type, name)`, so entity deduplication still works *within* a tenant (the whole point of deterministic IDs) but never collapses two tenants' nodes into one.
4. **Every tenant-scoped store gets the same migration shape.** A new `tenant_id` column, `NOT NULL DEFAULT 'default'`, added via `CREATE TABLE ... DEFAULT` for fresh databases and a defensive `ALTER TABLE ... ADD COLUMN` (wrapped to tolerate "already exists") for pre-existing ones — applied identically to `VENUAI/auth.py`'s `users` table and both knowledge-graph backends (SQLite, Postgres). The JSON-file-backed stores (document/dataset metadata) have no schema to migrate — they just default a missing key at read time.

## Workflow
See the registration diagram above for tenant creation/joining. For an authenticated request: `get_current_user()` builds an `AuthenticatedUser` whose `tenant_id` comes from the `users` table row (never from a client-supplied claim), and every downstream access-control check reads `current_user.tenant_id` — never anything the caller can set directly on a request body.

## Inputs
`organization_name` (creates a tenant) or `tenant_id` (joins an existing one) on `POST /auth/register`, both optional. `GET /auth/tenant` takes no input beyond the bearer token.

## Outputs
`UserOut.tenant_id` on register/login/refresh/me. `TenantOut {tenant_id, name}` on `GET /auth/tenant`.

## Dependencies
- [`authentication.md`](authentication.md) — the identity/JWT layer this builds on; `AuthenticatedUser.tenant_id` is populated the same way `role` already was.
- [`../AI/knowledge-graph.md`](../AI/knowledge-graph.md) — the graph store's tenant-scoping, including the `Entity.make_id()` collision fix.
- [`../AI/data-analyst.md`](../AI/data-analyst.md) — dataset tenant isolation.

## Interactions with Other Services
None external — tenants are a purely internal partitioning concept, not tied to any identity provider or billing system.

## Security Considerations
- The single most important invariant: **nothing about tenant membership is ever read from client input on an authorized request** — only from the JWT-derived `AuthenticatedUser.tenant_id`, which itself traces back to the `users` table row set at registration/tenant-join time.
- A cross-tenant resource lookup returns 404, never 403, to avoid leaking existence across the tenant boundary (see Design Principle 1).
- Registering with an unknown `tenant_id` (not `organization_name`) fails with 404 rather than silently creating the tenant — joining a typo'd tenant id must never succeed.

## Performance Considerations
No new indexes beyond `tenant_id` on the two knowledge-graph tables — the JSON-file-backed document/dataset stores already do a full in-memory scan per request (see their own docs' Performance Considerations), and an added `.get("tenant_id", "default") == x` check per item doesn't materially change that cost.

## Quotas and Member Management (added after initial Phase 4 pass)
Every tenant row now carries `max_users`/`max_documents`/`max_datasets` (SQLite defaults 20/100/50, migrated onto pre-existing `tenants` rows the same `ALTER TABLE` way `tenant_id` itself was added to `users`). Enforcement points:
- `auth.create_user()` checks `max_users` whenever a user *joins* an existing tenant (default tenant, or an explicit `tenant_id`) — not when `organization_name` creates a brand-new tenant, which trivially has room for its first member.
- `_enforce_document_quota()`/`_enforce_dataset_quota()` in `rag_server.py` check before `POST /upload`, `POST /upload/multimodal`, and `POST /datasets/upload` do any real work (file write, parsing) — a quota-exceeded request fails fast with 403, not after consuming upload bandwidth.

Admin-only management surface:
- `PATCH /tenants` — rename and/or change any of the three quota fields on the caller's own tenant. Like every other admin endpoint in this phase, there is no way to target a tenant other than the caller's own (see Design Principle 2) — this is tenant self-service, not platform administration.
- `GET /tenants/members` / `POST /tenants/members` — list the tenant's members, or add one directly. `POST /tenants/members` is a **deliberate simplification of a real invite flow**: there is no email system in this project (see [`authentication.md`](authentication.md) Future Improvements), so there is no invite link to send — the admin creates the account and shares the password with the new member out-of-band, the same way `seed_demo_users()` provisions the two built-in demo accounts. A production version of this would generate a one-time invite token and email it instead of handing the admin a plaintext password to relay.
- `GET /tenants/usage` — member/document/dataset counts against quotas; visible to any tenant member (aggregate counts aren't sensitive the way per-user data is), not just admins. Backs the Research Workspace's "Team & Quotas" tab.

No member-removal endpoint exists yet — see Future Improvements.

## Future Improvements
1. **Member removal/deactivation** — `POST /tenants/members` adds members; there is no corresponding "remove a member" endpoint. Deliberately not built in this pass: removing a user raises real questions (what happens to their documents/datasets — reassign to another owner? delete? orphan with `owner_id` pointing at a deleted user?) that deserve their own design decision, not a quick addition.
2. A real invite-token/email flow in place of `POST /tenants/members`'s "admin shares the password directly" simplification, once a real SMTP/email provider exists in this project.
3. Per-tenant request-rate quotas (today's `slowapi` limits are global, not tenant-aware) and storage-size quotas (today's quotas count rows/members, not bytes).
4. A real cross-tenant superuser role for platform operators, distinct from the per-tenant `admin` role — deliberately not built in this phase, since "admin sees everything" silently becoming "sees everything in every tenant" is exactly the kind of accidental privilege escalation Design Principle 2 exists to prevent.

## Notes for Developers
If you add a new resource type with an `owner_id`/`classification` access-control pair (following the existing documents/datasets/knowledge-graph pattern), it needs a `tenant_id` field and a tenant check *before* the owner/classification check, following Design Principle 1 — copy the shape of `_check_dataset_access()` in `VENUAI/rag_server.py`, not just the owner/classification half of it.

## Authorization layer (security stream S2)
All access decisions live in `VENUAI/authz.py`; handlers do not re-implement them.

- **Visibility:** a document/dataset-like record is visible to its owner, to a tenant admin (role `admin` is per tenant, never cross-tenant) and, if classified `PUBLIC`, to every member of the same tenant. Anything in another tenant behaves as if it did not exist.
- **404, not 403:** `authz.check_doc_access` / `check_dataset_access` return the record or raise 404 with the same body as a missing id (no existence oracle). 403 is only used when the caller can see the resource but may not perform the action (e.g. a non-owner deleting a PUBLIC document) and for role-gated routes (`require_tenant_admin`).
- **Safe lookups:** ids from the URL are never used to index a dict blindly; malformed ids give the same 404.
- **Super-admin:** `require_super_admin` accepts only users listed in `SUPERADMIN_USER_IDS` (empty by default = nobody). It guards server-wide state: `/approvals/pins*` (MCP tool pins are not per tenant).
- **Admin dashboards are tenant scoped:** `/analytics/dashboard` aggregates only the admin's tenant users; `/security/violations`, `/ops/*` filter by tenant; `/health` is `{"status":"ok"}` publicly and `/health/details` is admin-only (document count of the admin's own tenant).
- **Invites and members** (`VENUAI/tenant_admin.py`): `POST/GET /tenants/invites`, `DELETE /tenants/invites/{id}`, `PATCH /tenants/members/{id}` (role change), `DELETE /tenants/members/{id}`. Tokens are random, stored as SHA-256, single use, expiring, optionally bound to an email. `consume_invite(token, email) -> {"invite_id","tenant_id","role"} | None` is the function registration (S1) calls. A tenant always keeps at least one admin (409 otherwise); a role change/removal can never cross tenants (404). Removed members' documents stay owned by their id and remain visible to tenant admins.
- **Memory:** working memory is keyed by a length-prefixed `(tenant, user, session)`; SQLite `turns`, `sessions` (primary key `(tenant_id, user_id, session_id)`, legacy table migrated in place under tenant `default`) and `research_findings` carry `tenant_id`; semantic memory (Chroma `user_memory`) stores and filters `tenant_id`. Client session ids are normalised to `^[A-Za-z0-9_-]{1,64}$` (otherwise replaced by a digest); an absent id becomes a server-issued uuid. The default SQLite/Chroma paths are absolute (module-relative), independent of the working directory.
- **Vector store:** chunks carry `tenant_id`; `get_visible_doc_ids` returns an `authz.ScopedDocIds` (a list that also carries the tenant) and `retrieval.retrieve_documents` filters on `doc_id` AND `tenant_id` in Chroma, then post-filters every dense/BM25/region hit. Chunks ingested before tenant metadata existed belong to tenant `default` only.
- **Tool layer:** `policy_engine.check_tool_call` is deny-by-default (unknown tool/agent/role, malformed input) and writes denials to the audit trail (`authz.audit` -> `security_audit.log` when present); `EXECUTE_CODE` additionally needs `ENABLE_CODE_EXECUTION=1`; each request has a tool-call budget (`TOOL_CALL_BUDGET`, default 12); the module-level, identity-less tools in `agent_tools.py` refuse unless `ALLOW_UNSCOPED_TOOLS=1`.

Tests: `VENUAI/tests/test_sec_idor.py` (`pytest -m security`) is the IDOR matrix (A/B same tenant, C other tenant, tenant admins) plus ID guessing, mass assignment, role escalation, memory isolation, health leakage and policy tests; a route-inventory test fails when a route is added without being classified.
