# Business Policies — PENDING FINAL BUSINESS APPROVAL

None of these values are hardcoded. They are read from configuration (environment now; an owner-editable, audited `shop_settings` table from Phase 3). If a policy is unset, the code must refuse the dependent action or route it to the owner; it must never guess.

| Policy | Owner proposal | Setting | Status |
|---|---|---|---|
| Cancellation | Allowed before dispatch | `POLICY_CANCELLATION_RULE` | PENDING |
| Returns | 3 days, for incorrect, damaged or defective products | `POLICY_RETURN_WINDOW_DAYS` | PENDING |
| Refunds | After returned-product inspection | `POLICY_REFUND_AFTER_INSPECTION` | PENDING |
| Delivery coverage | Nationwide Myanmar | delivery zones (Phase 5) | PENDING |
| Delivery fees | Configurable per delivery zone | delivery zone table | PENDING |
| Preorder deposit | 30% | `POLICY_PREORDER_DEPOSIT_PERCENT` | PENDING |
| COD maximum order value | Configurable | `POLICY_COD_MAX_ORDER_MMK` | PENDING (amount unset) |
| COD manual review | High-value orders reviewed manually | `POLICY_COD_MANUAL_REVIEW_ABOVE_MMK` | PENDING (amount unset) |

Launch gate: `POLICIES_APPROVED_FOR_LAUNCH` must be set to `true` by the owner; a production start check will be added in Phase 6.
