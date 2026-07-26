# SafeAct — Product Brief

> **One line:** The safety checkpoint every AI action passes through before it happens —
> so enterprises can finally let their agents touch real money and real customers.

- **Product name:** SafeAct
- **Domain:** safeact.ai (companion redirect: getsafeact.com)
- **Open-source core:** `safeact` (this repo) — the free library and developer front door
- **Status:** core library built + mutation-tested; enterprise layer designed, not yet built
- **Date:** 2026-07-26

---

## 1. The problem

Companies are handing real, side-effecting work to AI agents: send the email, issue the
refund, approve the claim, update the system of record. The blocker is not intelligence —
it is **trust**:

- 97% of companies launched an AI agent in the past year; **only ~12% reached production at scale.**
- The failure cause is consistently *integration, ownership, and verifiability* — not model quality.
- One crash-and-retry makes an agent send the same email dozens of times.
- One low-confidence call issues a large refund with no approval and no paper trail.
- Risk and compliance cannot sign off on an action they cannot gate, prove, or undo.

In regulated workflows the bar is now explicit (NAIC AI model bulletin adopted by 25+ US
states; EU AI Act mandates human oversight + explainability). An agent is only shippable if
you can produce, in minutes: the inputs, the decision, the policy in force, and the human
sign-off. Miss one artifact and compliance unwinds the agent before it earns a dollar.

**SafeAct closes the trust gap, not the intelligence gap.**

## 2. What it is

A control plane that sits between *any* AI agent and the systems of record it writes to.
Every proposed action passes through four guarantees:

| Guarantee | Plain meaning |
|---|---|
| **Checked** | Action is checked against rules — in code, so the model can't prompt its way around them. |
| **Held when unsure** | Below a confidence floor → routed to a human to approve/reject, not fired blindly. |
| **Exactly once** | Crash / restart / retry → the action still happens once; if truly unknowable, it says so. |
| **Provable** | Tamper-evident receipt: what happened, why, which policy version, who approved it. |

## 3. Architecture — two halves, both already prototyped

```
┌──────────────────────────────────────────────────────────────┐
│  SafeAct Control  (paid, enterprise) — the sellable product    │
│  rules-as-code policy engine · human-approval inbox ·          │
│  tamper-evident audit · one-click "prove it" export ·          │
│  multi-tenant · roles · SSO                                    │
│    ← prototyped in FuelSense (copilot/email/confidence-gate.ts)│
├──────────────────────────────────────────────────────────────┤
│  SafeAct Core  (free, OSS — this repo, `safeact`)            │
│  exactly-once execution · honest IN_DOUBT state ·             │
│  saga compensation / rollback                                  │
│    ← BUILT, Python, mutation-tested                            │
└──────────────────────────────────────────────────────────────┘
```

The correctness half (exactly-once, in-doubt, saga) is the `safeact` library, done.
The governance half (policy, confidence gate, hold-for-human, audit) exists as a working
pattern in the FuelSense co-pilot and needs to be generalized, hardened, and made
multi-tenant. **The product is the fusion of the two, pointed at regulated buyers.**

### Deployment shape (supports "both languages from day one")

Build **service-first**: the brain (rules, gate, approval inbox, audit, export) lives in one
language-neutral service. Each language gets a **thin SDK** (Python, TypeScript) that calls
the service and holds-or-runs the action. A second language is then days, not months. The
offline exactly-once core stays available as a local library (no server required).

## 4. Business model — open core

- **SafeAct Core — free & open source.** Developers adopt it in ~10 lines. This is the wedge.
- **SafeAct Control — paid.** Human approvals, audit, regulator export, roles, multi-team —
  the layer risk/compliance/platform leads need to say yes.
- Playbook precedent: dbt, Temporal, Vanta — free tool earns adoption; the control/governance
  layer is what enterprises pay for.

**Buyer:** platform / ML-engineering leads (adopt the library) + risk / compliance leads
(sign the Control contract). Inbound signal: repo stars from `.com` email domains.

## 5. v1 scope — the demo *is* the spec

Goal: one 90-second demo where an AI action is caught, held for a human, survives a crash,
and produces an auditor-ready receipt. Build the smallest thing that delivers that.

**Reuse (do not rebuild):**
- Exactly-once + in-doubt + saga → `safeact` core (done).
- Confidence-floor + hold-for-human logic → lift the pattern from FuelSense's confidence-gate.

**Build (~4 new pieces):**
1. **Rules + confidence gate** — before an action runs, check "allowed?" and "confident enough?"; else hold.
2. **Approval inbox** — a web page where held actions queue for human Approve / Reject.
3. **Signed audit receipt** — every action logged tamper-evidently (what / why / who approved).
4. **"Prove it" export** — one click returns the full record for an action.

**Demo script (~90s):** $50 refund → cleared & logged · $50,000 refund → held · human
approves → runs · power pulled mid-action → no double refund · "Prove it" → clean receipt.

**Beachhead:** refunds / payments (universally understood, money = instant stakes, reuses the
existing refund example). **Expansion:** insurance claims / denials, where human sign-off is
legally mandated.

## 6. Explicitly out of v1

Keep scope from ballooning. Deliberately *not* in the first pass:

- Lease/heartbeat to separate a live concurrent loser from a crashed predecessor (documented
  edge in the core; an extension point, not built).
- Multi-framework auto-adapters beyond the two thin SDKs.
- Advanced RBAC / org hierarchy beyond basic roles.
- Anything that isn't required to make the 90-second demo land.

## 7. Launch plan

**Phase 0 — Foundation.** Register safeact.ai; one-page site (headline, demo video, four
guarantees, install line, one email capture); make the `safeact` repo public and polished.

**Phase 1 — Developer wedge (organic, not ads).** Show HN ("stop your agent sending the same
email 40 times"); builder posts in r/LocalLLaMA, r/LangChain, r/AI_Agents, r/LLMDevs; Product
Hunt; build-in-public on X/LinkedIn; a "why durable execution doesn't make actions safe" post.

**Phase 2 — Demand capture.** Demo video as the hub; **Google Search ads on intent terms**
("idempotent AI agent", "AI agent audit trail", "human in the loop approval") — higher ROI
than Reddit display ads; comparison content (SafeAct vs Temporal vs DBOS).

**Phase 3 — Enterprise motion (parallel).** 2–3 design partners with stalled agents; free
Control in exchange for a logo + quote; LinkedIn outbound to platform + risk/compliance leads.

**On paid ads:** yes, but **Google Search (intent) over Reddit (display)**, and only *after*
the organic dev launch has produced a starred repo and a demo people already share. Ads
amplify proof; they can't manufacture it.

## 8. Open decisions

- Final trademark/availability check on safeact.ai at a registrar before committing spend.
- Pricing model for Control (per-seat vs per-action-audited vs platform fee).
- First design-partner target (fintech refunds vs insurance claims).
