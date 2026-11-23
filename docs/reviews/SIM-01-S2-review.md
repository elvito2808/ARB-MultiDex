# SIM-01 / Review-1 — Independent Quote contract review

| Field | Value |
| --- | --- |
| Role / reviewer / supervisor | S2 — Independent Reviewer (Codex) / Agent 0 supervises |
| Base | `agent-governance@9acb21a38c4cacbef8af061ab86a09f06913dec2` |
| Reviewed | `sim-01-quote-contract@c63f0c57740013d297c2af819e2829c8ea4f1e87` |
| Result | **CHANGES_REQUIRED** |
| Result SHA / result link | The commit containing this report. Resolve with `git rev-parse sim-01-s2-review` or use the [review branch](https://github.com/elvito2808/ARB-MultiDex/tree/sim-01-s2-review) once published. |
| Changed file | `docs/reviews/SIM-01-S2-review.md` |

`git ls-remote https://github.com/elvito2808/ARB-MultiDex.git refs/heads/sim-01-quote-contract refs/heads/agent-governance` returned both expected SHAs. The fetched target had the governance base as its sole parent. The base-to-target diff contained only `docs/design/quote-contract.md`. Reviewed under [AGENTS.md](../../AGENTS.md), [roles](../agents/roles.md), [responsibilities](../agents/responsibilities.md), [handoff](../agents/handoff.md), and [open decisions](../agents/decisions.md). The report identifies the result commit by reference because embedding its own SHA would change that SHA.

## Findings and requested changes

Line references are to the reviewed [Quote contract](../design/quote-contract.md).

| Area | Assessment and recommended revision |
| --- | --- |
| BUY/SELL and fixed base — Request and response, lines 18–20 | Exact-output BUY and exact-input SELL are the right semantics for DEX §§3, 6 and Opportunity §6. Specify that BUY delivers the full requested base amount after base-denominated fees and SELL debits exactly that amount including any base-denominated fee, or reject where the fee model cannot preserve that invariant. Validate each side against the relationship's configured volume as well as against its peer. A pair of matching Quotes for the wrong volume currently passes the written pair rule. Derive unit price as a rational or a separately rounded display value. |
| Numeric representation — lines 18–20 | Positive integer strings and exact cross-scale comparison are sound. Define the integer string grammar, canonical asset scales or exact normalization rules, magnitude/scale bounds, and settlement rounding at the adapter boundary, including amounts that round to zero or become nonpositive after fees. Specify the handling of an unrepresentable request or insufficient precision. Matching should compare normalized quantities, including when source asset scales legitimately differ. |
| Settlement and fees — lines 18–20 and 28–35 | SELL net minus BUY total fits both formulas in Opportunity §6. The final amount alone cannot show coverage required by DEX §§6, 8 or distinguish settlement cash flow from an economic amount adjusted for costs paid in another asset. Define both concepts where they differ and expose applied fee components or an unambiguous reference to their policy, original units and any settlement valuation provenance. Explain how pool fees, price impact already expressed in the price, gas, and other costs enter the total exactly once. Declare the required fee policy as a request/configuration binding known to both sides and give unknown required fees an explicit error mapping. Different policies for the same market and size must stay distinguishable. |
| Market and mapping — lines 18, 32, 35, 37 | Source-scoped IDs and explicit 1:1 settlement mapping prevent symbol-only comparisons. Specify how `sourceId` resolves uniquely to the DEX, network, pair and selected pool, directly or through a stable configuration reference. Require comparison against the request and relation's mapped base and settlement identities and active configuration version. Define base equivalence as one-for-one or require a separately defined normalization rule. Treat approved 1:1 settlement equivalence as a comparison assumption. Reject a same-symbol asset on the wrong network or a Quote from a different pool even if all amounts match. See DEX §§2–4, 8, Opportunity §§4–5, and the reference counterparty §§3–5, 8–10. |
| Observation and freshness — lines 22, 30–35, 70 | Using market observation time and preserving it on reread/reconnect are correct. Age equality rejects and skew equality permits under the written inequalities. Define the UTC wire format, precision, source of evaluation time, configuration bounds and missing-policy behavior. Specify how a multi-input observation is dated so a newer fee read cannot mask stale pricing data. Require the producer to withhold stale/invalid successes and S2 to recheck cached samples at pairing time, consistent with DEX §§7, 9, Connections §9 and Opportunity §§8–9. Define when a disconnect invalidates cached Quotes and how recovery confirms a valid observation. |
| Errors and correlation — lines 26–37 | All six listed codes stop the dependent comparison. Source-only errors are ambiguous with concurrent BUY/SELL or volumes on the same market unless the transport guarantees request binding. Define a correlation mechanism for every result, including malformed requests without a usable source ID. Define success/error shapes and ownership of request validation, adapter errors and pair rejection. Specify whether wrong-market responses are `INVALID_QUOTE` or `INCOMPARABLE_QUOTES`, how request echo mismatches differ from pair mismatches, and which reason takes precedence when multiple checks fail. Separate or clarify retry behavior for outage versus insufficient depth under `SOURCE_UNAVAILABLE`. |
| Mode and integration — lines 18, 31, 37, 72–83 | Require both the request and every accepted leg to be `SIMULATION` and reject mixed-mode pairs, per Security and Testing §8. Require isolation of independent relationships and cache reuse only across matching mode, market, side, asset identities, exact size and applicable fee/configuration policy. Two complementary samples exercise one direction only. Add the opposite pairing and keep Opportunity §7's strict profit threshold and §11's execution guard at the opportunity boundary. A quote spread alone establishes neither threshold eligibility nor execution readiness. |

## Independent offline replay

Run from the review branch root. This one-off check parses the committed fixture, applies each scenario independently, and asserts expected results using exact fractions and a fixed evaluation clock. It models the predicates exercised by these six scenarios. The mapping sets represent the explicit fixture mappings, and the fixture supplies the successful response bodies without request envelopes. The changed SELL amount exercises the pair-volume check in isolation. With the original request held fixed, response echo validation could also reject it earlier once that behavior is specified.

```sh
python3 - <<'PY'
import json
from datetime import datetime
from fractions import Fraction
from pathlib import Path

document = Path("docs/design/quote-contract.md").read_text()
buy, sell = json.loads(document.split("```json\n", 1)[1].split("```", 1)[0])
stamp = datetime.fromisoformat
base_ids = {q["baseAssetId"] for q in (buy, sell)}
settlement_ids = {q["settlementAssetId"] for q in (buy, sell)}

def amount(q, kind):
    return Fraction(int(q[kind + "Units"]), 10 ** q[kind + "Scale"])

def assess(a, b, now="2026-11-23T00:00:02Z", skew=2, mapping=base_ids):
    for q in (a, b):
        if "error" in q:
            return q["error"]
        age = (stamp(now) - stamp(q["observedAt"])).total_seconds()
        if age < 0:
            return "INVALID_QUOTE"
        if age >= 5:
            return ("STALE_QUOTE", q["side"])
        if q["baseAssetId"] not in mapping or q["settlementAssetId"] not in settlement_ids:
            return "ASSET_MISMATCH"
    if abs((stamp(a["observedAt"]) - stamp(b["observedAt"])).total_seconds()) > skew:
        return "QUOTE_TIME_SKEW"
    if (a["side"], b["side"]) != ("BUY", "SELL") or amount(a, "base") != amount(b, "base"):
        return "INCOMPARABLE_QUOTES"
    cost, proceeds = amount(a, "settlement"), amount(b, "settlement")
    return amount(a, "base"), cost, proceeds, proceeds - cost

cases = [
    ("baseline", assess(buy, sell), (2, Fraction("101.25"), Fraction("104.50"), Fraction("3.25"))),
    ("freshness", assess(buy, sell, now="2026-11-23T00:00:05Z"), ("STALE_QUOTE", "BUY")),
    ("mapping", assess(buy, sell, mapping={buy["baseAssetId"]}), "ASSET_MISMATCH"),
    ("skew", assess(buy, sell, skew=0), "QUOTE_TIME_SKEW"),
    ("volume", assess(buy, dict(sell, baseUnits="201")), "INCOMPARABLE_QUOTES"),
    ("outage", assess(buy, {"sourceId": sell["sourceId"], "error": "SOURCE_UNAVAILABLE"}), "SOURCE_UNAVAILABLE"),
]
for name, actual, expected in cases:
    print(f"{name}: {actual}")
    assert actual == expected, (name, expected, actual)
PY
```

| Documented scenario | Expected | Actual |
| --- | --- | --- |
| Sample BUY and SELL | 2 base units each, BUY 101.25, SELL 104.50, spread 3.25 | Exact fractions 2, 405/4, 209/2, 13/4 |
| Freshness at 00:00:05Z | BUY is stale at age 5 s, SELL is still fresh at age 4 s | `('STALE_QUOTE', 'BUY')` |
| Drop second base mapping | `ASSET_MISMATCH` | `ASSET_MISMATCH` |
| Set skew limit to 0 s | `QUOTE_TIME_SKEW` for observed gap of 1 s | `QUOTE_TIME_SKEW` |
| Set SELL baseUnits to 201 | `INCOMPARABLE_QUOTES` for 2.01 vs 2 | `INCOMPARABLE_QUOTES` |
| Replace SELL with outage error | `SOURCE_UNAVAILABLE` | `SOURCE_UNAVAILABLE` |

All six assertions passed. No calculation errors were found in these inputs. The committed fixtures contain final amounts only, so the replay cannot verify underlying fee arithmetic. The target has no adapter or opportunity implementation. Integration, pricing-model validation, mode isolation at runtime and end-to-end tests were unexecuted.

## Missing cases and remaining risks

- Specify outcomes and add deterministic fixtures for malformed/future timestamps, just below/at/above age and skew limits, reread and reconnection with old data, and a stale pricing observation paired with a fresh cost input.
- Cover invalid mode and mixed mode, invalid side, unknown or wrong source, wrong network/pool, missing settlement mapping, unapproved 1:1 equivalence and same-symbol collisions. Include out-of-order responses to concurrent requests for one source.
- Cover zero, negative, fractional or malformed units, invalid and excessive scales, equal values at different scales, unsupported precision, and equal leg quantities that differ from configured size.
- Add a positive fee breakdown and cases for required fee missing, base-denominated fees, costs denominated outside settlement, insufficient depth, and nonpositive net proceeds. Establish expected rounding and confirm included costs are accounted for once.
- Add opposite-route Quotes, losses, profit equal to threshold, wrong leg pairing, and independent relationships with one invalid input. The latter tests opportunity behavior under Security and Testing §17. Adapter fixtures for pool/network binding, depth, price impact, cost coverage and error propagation are needed under §§15–16. Reuse and reconnect must preserve observation time and policy identity.

Agent 0 should record **CHANGES_REQUIRED** for the reviewed SHA, return the cited changes to S1 under SIM-01 with a new revision, and request S2 review of the revised contract and distinguishing fixtures before acceptance as an opportunity-engine input.
