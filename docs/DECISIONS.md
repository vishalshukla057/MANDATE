# Architecture & Design Decisions (docs/DECISIONS.md)

## Decision 1: SQLite for local dev & testing, PostgreSQL ready
- Local development and test suites use SQLite with SQLite-compatible JSON and DateTime types.
- Production uses PostgreSQL via standard SQLAlchemy connection strings.
- All models include `tenant_id` column for multi-tenant isolation, default single-tenant `default_tenant`.

## Decision 2: PayPal Gateway Abstraction (Fakeable Layer)
- Abstract base class `PayPalGateway` with implementations:
  - `LivePayPalGateway`: real async HTTP calls to PayPal Sandbox API (`https://api-m.sandbox.paypal.com`), OAuth client-credentials token caching, exponential retries on 5xx, `PayPal-Request-Id` idempotency header derivation, `PayPal-Debug-Id` capture in audit logs.
  - `FakePayPalGateway`: deterministic, in-memory state engine for invoices, payments, webhooks, payouts, disputes, and orders. Enables 100% offline, zero-network CI test execution and demo reliability.

## Decision 3: Hash-Chained Tamper-Evident Audit Log
- Audit log entries calculate: `hash = SHA256(prev_hash + canonical_json(entry_fields))`.
- Genesis block starts with `prev_hash = "GENESIS_HASH_00000000000000000000000000000000000000000000000000000000"`.
- `/audit/verify` re-computes hashes across sequential rows to mathematically verify the chain.

## Decision 4: Policy Engine as Sovereign Guardrail
- No money-moving endpoint (invoice sending above threshold, reminder dispatch within quiet hours or dispute locks, discounts > max %, payouts, refunds, offline payments) can execute directly from the agent or unauthenticated callers.
- All write proposals flow through `PolicyEngine.evaluate()`, resulting in `allow`, `require_approval`, or `block`.
- Actions needing approval enter the `approvals` queue with machine-readable `signals` and human-readable `reasoning`.

## Decision 5: Logical Clock Service for Time-Travel Simulation
- All time-sensitive logic (invoice overdue detection, reminder cadence, offer expiry, aging buckets, cash-flow forecasting) queries `ClockService.now()`.
- Simulation endpoint `POST /simulate/time-travel { "days": N }` advances the logical offset instantly, allowing judges to test multi-week collection lifecycles in seconds without real-world waiting.

