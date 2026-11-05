# Collections Copilot: REST API Documentation (`docs/API.md`)

All private routes require the `X-API-Key` HTTP header.
Public routes: `/health`, `/docs`, `/redoc`, `/webhooks/paypal`, `/ui`, and `/`.
Standard Error Format:
```json
{
  "error": {
    "code": "PAYPAL_ERROR",
    "message": "Error description",
    "details": [],
    "correlation_id": "cid-xxxxxxxxxxxx"
  }
}
```

---

## 1. System & Health
- `GET /health`: Health status, active environment, PayPal mode, logical clock time, kill switch state.
- `GET /` or `GET /ui`: Serves the Stitch Sovereign Heritage UI dashboard.

---

## 2. Agent & Ingestion
- `POST /agent/message`:
  - Body: `{"message": "Invoice Acme 40 hours at $85, net 15", "conversation_id": "optional"}`
  - Executes tool loop with Claude / offline fallback, passes through Policy Engine, returns run summary and action status.
- `POST /agent/ingest/text`:
  - Body: `{"text": "..."}` -> returns parsed structured invoice draft.
- `POST /agent/ingest/email`:
  - Body: `{"sender": "...", "subject": "...", "body": "..."}`.
- `POST /agent/ingest/voice`:
  - Body: `{"transcript": "..."}`.
- `GET /agent/runs`: Paginated execution history.
- `GET /agent/runs/{id}`: Detailed trace of tool executions and reasoning.

---

## 3. Approvals Queue (Dual-Key Trust Layer)
- `GET /approvals?status=pending_approval`: List queued actions requiring human sign-off.
- `POST /approvals/{id}/approve`: Execute the action against PayPal API.
- `POST /approvals/{id}/reject`:
  - Body: `{"reason": "Declined by operator"}`
- `POST /approvals/{id}/edit`:
  - Body: `{"args": { ... }}` -> edits payload and re-evaluates policy.

---

## 4. Invoices
- `GET /invoices?status=sent&client_id=...`: Filter and list invoices.
- `POST /invoices`: Create new invoice draft in DB and PayPal.
- `GET /invoices/{id}`: Full invoice details, line items, and payment timeline.
- `POST /invoices/{id}/send`: Send invoice to payer.
- `POST /invoices/{id}/remind`: Send custom reminder via PayPal remind API.
- `POST /invoices/{id}/cancel`: Cancel invoice.
- `POST /invoices/{id}/payments`: Record external payment.

---

## 5. Clients
- `GET /clients`: List all clients.
- `POST /clients`: Create client.
- `GET /clients/{id}`: Get client profile.
- `PATCH /clients/{id}`: Update client details.
- `GET /clients/{id}/history`: Payment history, lifetime billing, average days to pay.
- `GET /clients/{id}/risk`: Risk score (0-100), risk label (`reliable` | `watch` | `risky`), and explainable signals.

---

## 6. Contractors & Payouts
- `GET/POST /contractors`: Manage contractors.
- `GET/POST /payout-rules`: Setup automatic split rules (`on_invoice_paid`, `percent_of_invoice`, or `fixed`).
- `GET /payouts`: List payouts.
- `POST /payouts/{id}/approve`: Submit approved payout batch to PayPal.

---

## 7. Disputes Copilot
- `GET /disputes`: List disputes.
- `GET /disputes/{id}`: Get dispute details with compiled evidence pack, win likelihood estimate, and drafted response.
- `POST /disputes/{id}/evidence`: Upload additional evidence.
- `POST /disputes/{id}/regenerate-draft`: Regenerate factual response.
- `POST /disputes/{id}/submit`: Submit response to PayPal API.

---

## 8. Financial Analytics
- `GET /analytics/summary`: Total outstanding, overdue, DSO, collected this month, top late payers.
- `GET /analytics/cashflow?days=30`: Probabilistic weekly cash flow forecast with confidence bands.
- `GET /analytics/aging`: Overdue aging breakdown (0-30, 31-60, 61-90, 90+ days).
- `GET /analytics/agent-stats`: Observability metrics on auto-executions, approvals, blocks, and approval rates.
- `POST /analytics/ask`: Natural language financial queries (`{"question": "Who owes me the most money?"}`).

---

## 9. Policy & Tamper-Evident Audit
- `GET /policy`: View active policy thresholds.
- `PUT /policy`: Modify runtime policy thresholds.
- `POST /policy/kill-switch`: Emergency toggle to halt write operations.
- `GET /audit`: Paginated cryptographic audit log.
- `GET /audit/verify`: Cryptographic verification of SHA-256 hash chain from genesis root.

---

## 10. Webhooks & Sandbox Simulation
- `POST /webhooks/paypal`: Public endpoint for PayPal webhook push events.
- `POST /simulate/time-travel`: Advance logical clock by N days to test overdue transitions instantly.
- `POST /simulate/client-pays`: Simulate full, partial, or late payments.
- `POST /simulate/dispute`: Inject simulated dispute.
- `POST /simulate/seed-demo`: Populate demo dataset (6 clients, 15 invoices, 2 contractors, 1 dispute).
- `POST /simulate/reset`: Clean database wipe.

