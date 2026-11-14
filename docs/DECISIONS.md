# Owner decisions log

| ID | Decision | Status |
|---|---|---|
| B1 | Single-seller shop, not a marketplace | Approved |
| B2 | COD first automated method; digital payments manually verified until a provider/API is confirmed | Approved |
| U4 | Payment provider: MyanMyanPay API. Adapter stays PENDING until official API documentation is provided; deposit cancellation/refund rule undecided | Provider named; adapter and refund rule pending |
| B3 | Mock courier now; real Myanmar courier chosen after verification | Approved |
| B4 | Policies configurable, pending final approval (docs/POLICIES.md) | Pending |
| B5 | Gemini behind an AI provider abstraction; AI cannot change inventory, prices, payment verification or order state | Approved |
| B6 | Singapore VPS (about 2 vCPU / 4 GB), Docker Compose, HTTPS, automated backups | Approved; provider/domain pending |
| U5 | No extra order state for failed payment (unpaid orders go PENDING_PAYMENT to EXPIRED); partial cancellation cancels items, not a new order state; after ordering the bot sends a confirmation with a short explanation of next steps | Approved |
| Scope | No marketplace features and no "AI fashion studio" until specified | Assumed; awaiting explicit owner confirmation |

Open items: see docs/IMPLEMENTATION-PLAN.md (U1-U3, U6-U12).
