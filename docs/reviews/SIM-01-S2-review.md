# SIM-01 / Review-1 — Independent Quote contract review

| Field | Value |
| --- | --- |
| Supervisor / owner / reviewer | Agent 0 / S1 / Codex (S2, independent review) |
| Base | `agent-governance@9acb21a38c4cacbef8af061ab86a09f06913dec2` |
| Reviewed SHA | `c63f0c57740013d297c2af819e2829c8ea4f1e87` (`sim-01-quote-contract`) |
| Result commit | The commit introducing this report on [sim-01-s2-review-1](https://github.com/elvito2808/ARB-MultiDex/commits/sim-01-s2-review-1). Git assigns the SHA after the report is written. |
| Changed files | `docs/reviews/SIM-01-S2-review.md` |
| Result | **CHANGES_REQUIRED** |

GitHub resolved the target branch to the requested SHA before review. Comparing the supplied base SHA with the target showed one commit ahead, zero behind, and only `docs/design/quote-contract.md` changed.

## Contract and integration findings

Section references below refer to the [reviewed contract](../design/quote-contract.md).

1. **Fee coverage is unverifiable.** In **Request and response (درخواست و پاسخ)**, fixed-base BUY total and SELL net agree with DEX §6 and Opportunity §6. Both amounts support exact subtraction and the existing prohibition on double-counting. The response carries no fee coverage or policy identity, and none of the fixtures demonstrates cost inclusion. DEX §§6, 8 require the consumer to identify included costs. Two sources can report identical totals with different cost coverage. Require explicit included-cost coverage and a shared policy identifier or immutable policy reference. Define the conversion and rounding rule for required fees in another unit, the treatment of base-denominated fees so BUY delivers and SELL debits exactly the requested base amount, and the error for missing or unconvertible required fees. Add a cost-bearing fixture that shows the gross-to-final reconciliation with no duplicate deduction. Leave actual cost policy under D-03.

2. **Canonical quantities and request binding need explicit validation.** In **Request and response** and **Validity and errors (اعتبار و خطا)**, positive integer strings plus fixed asset scales can represent both volumes and settlement exactly. Define the accepted digits, canonical leading-zero rule, payload and scale bounds, and how an adapter handles amounts that cannot be represented at the configured scale. Validate responses against their own requests before pairing. A response with a changed volume for a 200-unit request is `INVALID_QUOTE`, while two independently valid requests for 200 and 201 units produce `INCOMPARABLE_QUOTES`. State whether zero net proceeds are prohibited and how they are rejected. Preserve exact amount arithmetic, and keep derived rates out of threshold comparisons so rate rounding cannot alter the decision. Update the 201-unit fixture to include its matching request.

3. **Source identity must resolve the configured market unambiguously.** In **Request and response** and **Validity and errors**, source-local asset identifiers and explicit base equivalence and 1:1 settlement mapping address symbol collisions and align with Opportunity §§4–5. DEX §§8–9 also require venue, network, pair and selected pool to be identifiable. Define `sourceId` as an immutable reference to exactly one configured market context, including those dimensions when applicable, or carry that context explicitly. Bind both legs to the relationship's configured market and mapping revision and specify rejection of wrong-context or obsolete-mapping results before price comparison. Distinguish an echoed market that disagrees with the request from a valid market outside the relationship. D-01 remains open.

4. **Specify where validity is enforced and when cached data becomes eligible again.** In **Request and response** and **Validity and errors**, `observedAt` is the observation time, reuse preserves it, equal-to-age-limit is stale, equal-to-skew-limit is allowed, and future observations are rejected. These rules match the configurable, independent freshness and skew checks in Opportunity §§8–9. Specify the accepted UTC timestamp grammar and precision, the clock reference and one evaluation instant per pair. DEX §§7–9 and Connections §9 place final quote validity at the producer. Require adapters to reject invalid or already stale data before publishing success and require the engine to recheck age on reuse and skew on pairing. State how disconnect invalidates affected cached quotes and what observation can restore eligibility, including replayed or out-of-order observations after recovery. Keep both limits configurable under D-03.

5. **Define the failure envelope, correlation and pair-level attribution.** In **Validity and errors**, `SOURCE_UNAVAILABLE` and source-local `INVALID_QUOTE` can be determined per request, whereas missing relationship mappings, `QUOTE_TIME_SKEW` and `INCOMPARABLE_QUOTES` can require pair context. The current two-field failure envelope can conflate simultaneous BUY and SELL operations at one source. Specify distinct success/error shapes and require request correlation in the envelope or guarantee it in the calling interface. Name the code field, assign producer and engine responsibilities for every rejection, and define precedence when invalidity, missing mapping and staleness overlap. Define the scope of each failure so an unrelated relationship or healthy direction can still evaluate when its inputs are valid.

6. **Keep mode and acceptance rules at the interface boundary.** In **Request and response** and **Two synthetic Quotes (دو Quote ساختگی)**, both examples are synthetic and declare `SIMULATION`, consistent with Security and Testing §8. Require the engine to reject mixed modes and prevent simulated quote or cache entries from becoming eligible for live decisions. For Opportunity §§6–9, compute both configured directions independently as SELL net minus BUY total, with each leg bound to the relationship's base volume. Accept only when estimated profit is strictly greater than the configured threshold and other eligibility checks pass. The example demonstrates one direction and its settlement difference. Add reverse-direction, below-threshold and exactly-at-threshold cases at the engine boundary.

## Offline checks

Retrieved the reviewed document by SHA and parsed the JSON block directly. Executed the calculation in an isolated reviewer scratch runtime with fixed UTC inputs and integer arithmetic. It made no network or adapter calls. The scratch runtime makes no project language choice.

| Case | Input or mutation | Expected | Actual |
| --- | --- | --- | --- |
| 1. Direction, base volume and amounts | Published pair, evaluation 00:00:02 UTC | BUY and SELL each 2 base, BUY 101.25, SELL 104.50, spread 3.25 | Same. At scale 2, 10450 − 10125 = 325. Ages 2 s and 1 s. |
| 2. Freshness boundary | Evaluate at 00:00:05 UTC with 5 s maximum age | `STALE_QUOTE` for BUY | BUY age 5 s, SELL age 4 s. BUY rejected. |
| 3. Asset mismatch | Remove the second source's base mapping | `ASSET_MISMATCH` | Pair rejected with that code. |
| 4. Observation skew | Set the maximum skew to 0 s | `QUOTE_TIME_SKEW` | Observations differ by 1 s. Pair rejected. |
| 5. Unequal base volumes | SELL 201 at scale 2 | `INCOMPARABLE_QUOTES` | Pair rejected with that code when both legs are treated as independently valid. A 201-unit response to a 200-unit request still needs a separate request-binding test. |
| 6. Source outage | Replace SELL with the second source's error | `SOURCE_UNAVAILABLE` | Error propagated and pair skipped. |

Three supplementary checks also matched their expectations. Skew exactly 1 s at a 1 s limit accepted the fixture. A SELL observation 1 s in the future returned `INVALID_QUOTE`. Changing SELL to 20 units at scale 1 preserved equality in pairwise arithmetic. The last check presupposes a source configured for that scale.

### Reproduction command

From a checkout at the reviewed SHA, run the following equivalent command. The executed scratch check used the GitHub-fetched text as its input and the same assertions. The nominal result encodes base scale, base units, settlement scale, BUY units, SELL units and spread units in that order.

```sh
node --input-type=module <<'JS'
import {readFileSync} from "node:fs";
const doc = readFileSync("docs/design/quote-contract.md", "utf8");
const fixtures = JSON.parse(doc.match(/\x60{3}json\s*([\s\S]*?)\x60{3}/)[1]);
const cases = ((fixtures) => {
  const [buy,sell]=fixtures;
  const at=s=>Date.parse("2026-11-23T00:00:"+s+"Z");
  const scaled=(units,scale,to)=>BigInt(units)*10n**BigInt(to-scale);
  function check(b,s,{now=at("02"),skew=2000,mapped=true}={}){
    if(s.code)return s.code;
    const times=[b,s].map(q=>Date.parse(q.observedAt));
    if(times.some(t=>!Number.isFinite(t)||t>now))return "INVALID_QUOTE";
    const stale=times.findIndex(t=>now-t>=5000);
    if(stale>=0)return "STALE_QUOTE ("+[b,s][stale].side+")";
    if(!mapped)return "ASSET_MISMATCH";
    if(Math.abs(times[0]-times[1])>skew)return "QUOTE_TIME_SKEW";
    const baseScale=Math.max(b.baseScale,s.baseScale);
    const volume=q=>scaled(q.baseUnits,q.baseScale,baseScale);
    if(b.side!=="BUY"||s.side!=="SELL"||volume(b)!==volume(s))return "INCOMPARABLE_QUOTES";
    const settlementScale=Math.max(b.settlementScale,s.settlementScale);
    const amount=q=>scaled(q.settlementUnits,q.settlementScale,settlementScale);
    return [baseScale,volume(b),settlementScale,amount(b),amount(s),amount(s)-amount(b)].map(String).join("/");
  }
  const ok="2/200/2/10125/10450/325";
  const cases=[
   ["nominal",ok,check(buy,sell)],
   ["freshness boundary","STALE_QUOTE (BUY)",check(buy,sell,{now:at("05")})],
   ["removed base mapping","ASSET_MISMATCH",check(buy,sell,{mapped:false})],
   ["zero skew limit","QUOTE_TIME_SKEW",check(buy,sell,{skew:0})],
   ["SELL 201","INCOMPARABLE_QUOTES",check(buy,{...sell,baseUnits:"201"})],
   ["source down","SOURCE_UNAVAILABLE",check(buy,{sourceId:sell.sourceId,code:"SOURCE_UNAVAILABLE"})],
   ["skew exactly 1 s",ok,check(buy,sell,{skew:1000})],
   ["future observation","INVALID_QUOTE",check(buy,{...sell,observedAt:"2026-11-23T00:00:03Z"})],
   ["equal volume different scales",ok,check(buy,{...sell,baseUnits:"20",baseScale:1})]
  ];
  return cases;
})(fixtures);
for (const [label, expected, actual] of cases) {
  console.log(label, {expected, actual});
  if (actual !== expected) process.exitCode = 1;
}
JS
```

Observed 9 of 9 expected results and zero assertion failures. The oracle checks isolated document rules. It does not validate payload grammar, request correlation, fee coverage, full market context, or the producer's reconnection behavior. No adapter, integration, end-to-end, or live tests ran.

## Missing cases and remaining risks

- **Input and identity.** Wrong mode, side, market/network/pool, or echoed volume; zero, negative, malformed and oversized amounts; invalid scales and timestamps; missing settlement mapping; identical symbols with unrelated identities; equal amounts represented at different settlement scales.
- **Economics and data quality.** Insufficient depth at exact requested volume, pool fee and price impact, separate or unavailable Gas/protocol costs, costs charged in base or a third asset, inconsistent policy coverage, and nonpositive sale proceeds. A numerically correct spread can still use incompatible cost assumptions.
- **Time and isolation.** Stale SELL and both stale, valid timestamps at the same instant, reconnect with replayed data, source error concurrent with healthy direction or independent relationship, and overlapping failures with deterministic attribution. The engine could combine valid-looking cached inputs from different contexts or suppress unrelated opportunities.
- **Future integration evidence.** Security and Testing §§15–17 require adapter market identity, price math, source recovery, both profit paths, threshold handling and worker isolation. The fixture checks cover only their documented subset. Execution deduplication, concurrency and actual adapter pricing remain future implementation checks.

## References and handoff

Reviewed [AGENTS.md](../../AGENTS.md), [roles](../agents/roles.md), [responsibilities](../agents/responsibilities.md), [handoff](../agents/handoff.md) and [open decisions](../agents/decisions.md) at the reviewed SHA. Applied [DEX §§6–9](../../DEX_Module_Requirements_v1.0.md), [Opportunity §§4–9 and §§15–16](../../Arbitrage_Opportunity_Module_Requirements_v1.0.md), [Security and Testing §§8 and 15–17](../../Project_Security_and_Testing_Requirements_v1.0.md), [KuCoin §§3–10](../../KuCoin_Module_Requirements_v1.0.md) and [Connections §§5–9](../../Connections_Module_Requirements_v1.0.md). S2 records the findings and Agent 0 owns acceptance.

**Next action for Agent 0.** Return SIM-01 to S1 for a new revision addressing findings 1–6 and request S2 review of the revised SHA. Coordinate fee and timing policy details under D-03 and market identity under D-01. Compare the package base with the intended destination before acceptance.
