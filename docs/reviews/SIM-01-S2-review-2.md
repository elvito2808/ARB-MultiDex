# SIM-01 / S2-Review-2 — Independent review of Revision 2

**Verdict: CHANGES_REQUIRED.** The six original findings have substantial corrective text. Findings 1–4 are resolved at the design level. Findings 5–6 remain partially resolved because the mixed-mode example does not distinguish the isolated pair gate from the full simulation-only validation pipeline. Blocking finding B1 below is a narrow expected-result/precedence inconsistency, not evidence that simulated data can reach a LIVE decision.

| Handoff field | Value |
| --- | --- |
| Task / review / revision | SIM-01 / S2-Review-2 / 2 |
| Supervisor / implementation owner / reviewer | Agent 0 / S1 — Market Data & Connectivity / S2 — Opportunity Detection |
| Consultant role | S5; no separate S5 attestation is claimed by this report |
| Repository | `elvito2808/ARB-MultiDex` |
| Governance branch / exact SHA | `agent-governance` / `9acb21a38c4cacbef8af061ab86a09f06913dec2` |
| Implementation branch / exact reviewed SHA | `sim-01-quote-contract-r2` / `4c8f77f9ade041bc3a8051758817c9a6aea79adf` |
| Review target | `docs/design/quote-contract.md` |
| Previous review branch / exact SHA | `sim-01-s2-review-1` / `0183514ed21f1ceb5130cc10b76a6036eb5b9190` |
| Previous report | `docs/reviews/SIM-01-S2-review.md` at the previous review SHA |
| Review branch | `sim-01-s2-review-2` |
| Authorized change | `docs/reviews/SIM-01-S2-review-2.md` only |
| Review date | 2026-11-23 UTC |
| Result reference | The commit introducing this report; its SHA is supplied in the handoff after commit creation |

## Scope and provenance

Reviewed the target by exact Git object, not a moving branch name. The target differs from the pinned governance tree only by `docs/design/quote-contract.md`. The review branch was based on the exact target; the remote review branch also pointed to that target when inspected. The original Review-1 report was read from its own pinned commit. Its absence from the implementation branch is a difference between branch histories, not a deletion made by this review.

Applied the pinned AGENTS.md, roles, responsibilities, handoff and decisions documents. Cross-checked DEX §§6–9, Opportunity §§4–9, Connections §§5 and 9, KuCoin §§3–10, and Security and Testing §§8 and 15–17. The reference scenarios do not authorize selecting providers, markets, networks or production policy values. D-01, D-02 and D-03 remain open. No contract, application, governance or previous-review changes are included. No live connectivity, wallet, signing, transaction or merge operations are part of this review.

All line references below identify [the exact reviewed contract](https://github.com/elvito2808/ARB-MultiDex/blob/4c8f77f9ade041bc3a8051758817c9a6aea79adf/docs/design/quote-contract.md), not this report's checkout after future changes.

## Criterion-by-criterion findings and Review-1 resolution

### 1. Fee coverage — original finding 1 resolved

Evidence: lines 32–36, 48–55, 72, 153–235 and 277–285.

- `feePolicyId` is a shared immutable relationship-policy reference. Required components are determined per market and side. Exactly one record per mandatory component is required, including known zero costs; at most 32 records are permitted. Missing or unknown required fees and unavailable, invalid, stale or unrepresentable conversions fail with `FEE_UNAVAILABLE`.
- `grossSettlementUnits` explicitly means the amount **after embedded costs and before separate costs**. BUY adds separate costs; SELL subtracts them. The consumer subtracts the two final amounts only. This definition avoids interpreting “gross” as an amount before every fee.
- Separate costs carry native asset, units, scale and a positive rational rate in settlement minor units per fee-asset minor unit. The settlement asset uses a 1:1 rate; another asset requires an immutable `rateRef`, checked against policy validity. Integer ceiling is applied once per component. There is no extra factor of ten in the documented minor-unit rate formula.
- Embedded costs carry only the three common fields. The producer must substantiate their attribution from the same pricing model. The consumer must not subtract their reported settlement equivalent again.
- Fixed-base BUY must price enough input to deliver the requested net base quantity. Fixed-base SELL must price the actual trade input such that trade input plus base fee equals the requested debit. A separately funded base charge is not permission to exceed that debit: it must still satisfy the fixed-base invariant and be modeled as embedded. Impossible exact quantity is `INVALID_QUOTE`; insufficient liquidity is `SOURCE_UNAVAILABLE`.
- Duplicate mandatory components violate exactly-once coverage. The checker treats this as `FEE_UNAVAILABLE` under the coverage rule. Malformed fields/treatment are structural `INVALID_QUOTE`. This duplicate-code interpretation is explicit here because no duplicate-fee expected row is supplied. The document clearly prohibits accepting the duplicate.
- With valid components, a final-total mismatch is `INVALID_QUOTE`. Reconciliation is skipped when a required component or valid conversion is unavailable. The specific zero-denominator example establishes `FEE_UNAVAILABLE`; the general nonpositive-value rule must not be used to reject explicitly allowed zero fees or override this fee-conversion case.

The reverse fixture demonstrates both treatments and correct reconciliation. The BUY base-fee rule is specified in prose but has no cost-bearing BUY base-fee fixture. That limits evidence for a future producer; it does not leave the fixed-base contract undefined.

### 2. Quantity and request binding — original finding 2 resolved

Evidence: lines 20, 26–28, 52, 63, 236–273 and 283–284.

Positive minor-unit values use ASCII strings matching `[1-9][0-9]{0,77}`. Zero is allowed only for expressly nonnegative fields and only as `"0"`. Leading zeros, signs, whitespace, decimals, exponents and non-ASCII digits are prohibited. Each message is at most 16,384 UTF-8 bytes, with unique JSON keys and defined fields. Identifiers are bounded ASCII tokens. Scales are unsigned decimal JSON integer tokens from 0 through 38 and must equal the registered local asset scale; lexical forms such as `2.0`, `2e0` and `-0` are not interchangeable accepted representations.

Every success echoes all 12 request fields exactly. The engine retains the original request and rejects changed volume or other echoes before pairing. A 201-unit response to q2's 200-unit request is `INVALID_QUOTE`; q5 now has its own matching 201-unit request and is independently valid, but pairing q1/q5 is `INCOMPARABLE_QUOTES`. Nonrepresentable native quantities and zero/negative final proceeds are rejected. Arbitrary-precision integer multiplication by powers of ten aligns scales; display rates cannot influence opportunity arithmetic. Threshold comparison must likewise use the common settlement unit.

The checker mutates every echoed field, exercises canonical quantity/scale boundaries, and distinguishes both volume cases. Its generic scale tests do not implement a complete registered-asset registry; registry consistency is a separate required producer/engine integration check.

### 3. Market identity — original finding 3 resolved

Evidence: lines 22, 48, 52, 61–63 and 286–288.

`sourceId` immutably identifies one venue/network/ordered native-asset pair/selected-pool context, with network and pool where applicable. Changing an identity dimension requires a new ID. `mappingRevision` is immutable within the relationship and identifies allowed markets, local scales, base equivalence and explicit 1:1 settlement mapping. Symbols cannot establish equivalence.

An echoed market or revision different from the request is request-level `INVALID_QUOTE`. Independently valid responses for a market outside the relationship, or an obsolete revision relative to the active pair configuration, fail with `INCOMPARABLE_QUOTES`. Missing base equivalence or settlement mapping is `ASSET_MISMATCH`. These checks precede arithmetic. The synthetic prose supplies mapping assumptions, not complete registry objects; the checker models those assumptions rather than proving real registry contents.

### 4. Freshness and cache — original finding 4 resolved at design level

Evidence: lines 40–44, 48–55 and 289–292.

UTC timestamps require exactly three fractional digits and `Z`, a valid Gregorian date, year 0001–9999, and seconds 00–59. Offsets, leap seconds and alternative precision are rejected. Higher-resolution observation times are floored to milliseconds. Producer validation occurs before publication; engine checks occur on acceptance and reuse, using a synchronized UTC reference and one evaluation instant per pair. Future observations are invalid; age 4999 ms is valid under a 5000 ms ceiling, age 5000 ms is stale. Skew equal to the ceiling is allowed; greater skew fails.

Epochs are scoped by market and mode and change on recovery. Disconnect or lost continuity invalidates cached and in-flight old-generation records in both producer and engine. Cache identity includes mode, relationship, mapping revision, market, side, asset identities/scales, volume, fee policy and epoch. Reuse retains the original observation and request identity; a new request requires a newly correlated envelope.

The producer retains its accepted observation-time watermark across disconnects. Older observations cannot replace the cache. Repeated events or different observations with the same timestamp cannot create new freshness. Reuse of the same valid snapshot across requests is expressly allowed. Recovery requires a snapshot acquired in the current epoch, observed no earlier than recovery and strictly later than the retained watermark, with all normal validity checks passing. The documented 03.001 snapshot at a 03.002 evaluation after 03.000 recovery satisfies these conditions.

**Concurrent invalidation review:** a naive sequence “read cached quote; disconnect/invalidate; publish decision from the local copy” would violate the current-generation consistency and per-decision eligibility requirements. The same applies to an old in-flight response arriving after recovery, or an old callback overwriting the retained watermark. The contract requires the correct invariant but does not choose locks, generation fencing, serialized events or a transaction mechanism. Future implementation must establish a consistent decision/publication point covering both inputs and connection state, then test these interleavings. The offline recovery predicate is sequential and is not evidence that races have been eliminated. No additional design blocker is raised on the choice of synchronization mechanism under D-02.

### 5. Error contract — original finding 5 partially resolved; B1 remains

Evidence: lines 20, 48–66 and 293–295.

Success and error envelopes are distinct. A Quote error has exactly `status`, `requestId`, `sourceId` and `code`; invocation-level correlation must survive unreadable payloads and missing input IDs. Invalid/missing input correlation fields become null, while the call identity retains attribution. This does not permit a response with null correlation for an otherwise valid request. Wrong-call responses and unknown codes are invalid and are never routed to another call.

The request order is `INVALID_QUOTE`, `SOURCE_UNAVAILABLE`, `STALE_QUOTE`, `FEE_UNAVAILABLE`, with snapshot-dependent checks performed only where data exists. Both leg failures remain attributed; the primary code follows this order and BUY wins ties. Every request failure precedes pair failures. Pair order is context/mode, asset mapping, exact configured volume, then observation skew.

Pair errors carry relationship, direction, both request IDs and code and stop only that direction. Request errors stop consumers of that input. Unrelated relationships and healthy inputs continue independently. A q2-only error need not stop q3/q4; a genuine sim-b disconnect invalidates **all** sim-b inputs in that mode, including the reverse BUY. The example makes that distinction explicit.

These rules resolve the original envelope and failure-scope gaps. B1 prevents a fully consistent expected error for the published mixed-mode scenario.

### 6. Simulation and consumer boundary — original finding 6 partially resolved; B1 remains

Evidence: lines 26, 61–68, 277–282 and 295–296.

Cache and decision paths are isolated by mode. The consumer checks mode against decision mode before price comparison or cache use. Simulation entries cannot be eligible in LIVE decisions. Current producers support only SIMULATION requests. No LIVE implementation is required or authorized by the enum or the boundary tests.

A→B uses BUY A / SELL B; B→A uses BUY B / SELL A. Each direction independently requires its configured exact base volume. Arithmetic is SELL final minus BUY final after exact scale alignment. Strict `>` is explicit: equality and below-threshold outcomes are valid non-opportunities. Both directions and all three reverse thresholds are now demonstrated.

The isolation rule is sound, but the mixed-mode expected result needs the stage distinction in B1. The checker verifies a SIMULATION pair cannot qualify for a LIVE decision; it does not exercise a real LIVE cache or runtime.

## Blocking finding B1 — mixed-mode expected result bypasses request precedence

**Severity:** medium; blocks acceptance of the contract's deterministic validation examples. **Owner:** S1. **Affected original findings:** 5 and 6.

**Locations:** lines 26, 52, 57, 61 and 295.

Line 26 supports only SIMULATION requests in this stage. Line 52 classifies unsupported mode as request-level `INVALID_QUOTE`, and line 57 requires independent request validation before pairing and gives any request error priority. Line 295 nevertheless expects `INCOMPARABLE_QUOTES` for an independent mixed-mode pair. Since the only mode values are SIMULATION and LIVE, such a pair necessarily includes an unsupported LIVE request. There is no independently valid mixed-mode pair in the current full pipeline.

**Concrete reproduction:** retain q1 as SIMULATION; change **both** q2's request and echoed response mode to LIVE. Echo binding still matches. Current request validation rejects q2 with `INVALID_QUOTE` before the pair stage. The reconstructed checker produces that result. In contrast, changing only q2's response mode is already correctly specified as `INVALID_QUOTE` for echo mismatch.

An isolated pair-gate function given hypothetical already-validated mixed-mode inputs can return `INCOMPARABLE_QUOTES`, as line 61 requires. That is a different test layer; it cannot establish that a LIVE request is independently valid in this revision. The current example does not label that distinction.

**Precise corrective guidance:** retain simulation-only support and request-before-pair precedence. Specify the full-pipeline mixed request/response-mode case as `INVALID_QUOTE` on the unsupported request. Separately label any `INCOMPARABLE_QUOTES` mixed-mode case as an isolated consumer pair-gate test with hypothetical prevalidated inputs outside the current producer-supported request set. Add distinct expected checks for response-only mode mismatch, request-and-response LIVE in the simulation-only pipeline, the isolated mixed-mode pair gate, and SIMULATION cache rejection for a LIVE decision. Do not enable LIVE support merely to make the example reachable.

No other blocking inconsistency was identified. This finding concerns deterministic classification and reproducible evidence; all readings reject the unsafe pair rather than creating an opportunity.

## Independent mathematical verification

| Check | Independent result |
| --- | --- |
| Forward settlement difference | `10450 - 10125 = 325` minor units = 3.25 settlement units at scale 2 |
| Forward threshold 300 | `325 > 300`: opportunity |
| Separate gas conversion | `ceil(5 * 1 / 2) = (5 + 2 - 1) // 2 = 3` **settlement minor units**, or 0.03 at scale 2 |
| Fixed-base reverse SELL | `199 + 1 = 200` base minor units, or 2.00 base at scale 2 |
| Embedded settlement effect | `10300 - 10245 = 55`; already reflected in the 10245 gross/final amount |
| Reverse BUY final | `10000 + 3 = 10003` |
| Reverse difference | `10245 - 10003 = 242` minor units = 2.42 settlement units |
| Threshold 241 | `242 > 241`: opportunity |
| Threshold 242 | `242 > 242` is false: no opportunity at equality |
| Threshold 243 | `242 > 243` is false: no opportunity below threshold |

The gas result is three minor units, not three whole settlement-asset units. Deducting either 3 or 55 again would be incorrect. The supplied nonlinear synthetic pricing values are premises; their subtraction is verified, not a production AMM pricing formula. For BUY base fees, the prose invariant is net receipt = trade receipt − base fee; the checker includes only the illustrative identity `201 - 1 = 200`, not a pricing fixture for that path.

## Test reproduction methodology and results

Read the JSON directly from `git show 4c8f77f9ade041bc3a8051758817c9a6aea79adf:docs/design/quote-contract.md`. Executed the Python standard-library checker reproduced below, with fixed UTC clocks and arbitrary-precision Python integers for every financial amount. Date deltas are used only over the small fixed fixture intervals. No provider, application, network or live data calls occur in the checker. Python here is a reviewer scratch tool, not a D-02 language decision.

**Observed result: 93/93 reviewer-authored assertions passed.** That means the explicit reviewer oracle behaved as stated; it does **not** mean all of the document's expected results were confirmed. In particular, the full-pipeline mixed-mode assertion expects and obtains `INVALID_QUOTE`, exposing the discrepancy with the unqualified `INCOMPARABLE_QUOTES` example in B1.

Coverage includes all five parsed fixture envelopes, both opportunity directions, the three reverse thresholds, gas ceiling, fixed-base and embedded-cost arithmetic, all 12 echo fields, changed versus independently unequal quantities, malformed/oversized quantity strings, precision and scale boundaries, duplicate JSON keys, the 16,384/16,385-byte payload boundary, missing/duplicate/zero fees, invalid conversion, reconciliation and double deduction, zero proceeds, malformed/future timestamps, exact age/skew boundaries, obsolete mappings, configured volume mismatch, error correlation and code rejection, overlapping-error ordering, source disconnect/old epoch, sequential recovery, and the decision-mode boundary.

**Assessment of S1's 37/37 claim:** line 298 states a result but supplies neither that executable checker nor a numbered assertion inventory or execution log. Five JSON fixtures and a table of compound mutations do not identify the exact 37 assertions or their test-layer assumptions. Exact reproduction of S1's run is therefore not established. The independent 93 checks are not a reconstruction of that exact count. Absence of the original harness is an evidence limitation, not itself a contract defect. The B1 discrepancy is independently identifiable from normative text and must not be hidden by a passing assertion total.

### Limits and unverified assumptions

- The checker is an executable model of selected rules, not a complete JSON schema validator or implementation conformance suite. Its standalone wire and scale-token checks are not a production parser. It does not validate every identifier token, every optional/forbidden cost field, or every registered asset's scale.
- Synthetic market/mapping/policy/rate facts are modeled from the prose. The removed-base and removed-settlement tests share a simplified mapping gate. No real venue/network/pool registry, rate observation store, rate expiry schedule or native asset conversion source was supplied.
- Fee coverage tests use the exact fixture policy. Duplicate mandatory costs are modeled as a coverage failure. The model is not a normative decision about optional nonmandatory costs or every malformed-fee overlap. Specific fee-unavailability rules take priority over a broad reading that would misclassify the documented zero denominator.
- Producer depth, actual pricing, embedded-cost attribution, BUY base-fee pricing, nonrepresentable native amounts and unavailable clocks are assessed as contract requirements, not empirically proven adapter behavior. The hypothetical BUY identity is arithmetic evidence only.
- The null-on-invalid-input correlation behavior, preservation of both error attributions, BUY tie attribution, full pair-error envelope, unrelated-relationship scheduling and same-timestamp snapshot identity were manually reviewed; the checker does not implement those complete systems.
- Recovery checks are predicates over fixed sequential state. Replay detection, watermark persistence, snapshot identity, in-flight cancellation, cache isolation and simultaneous disconnect/publication require stateful integration and concurrency tests after implementation. The checker has no actual cache and does not prove a cache miss.
- No separate S5 consultation, production/integration/end-to-end tests or live tests were performed. This review does not attest to operational readiness.

## Integration blockers and handoff to Agent 0

B1 is the only blocking finding for acceptance of this revision. Return the narrow expected-result/test-layer correction to S1 and review its new exact SHA. Findings 1–4 need not be redesigned; preserve the fee, identity, precision and recovery improvements. Recheck findings 5–6 after the correction, including both the producer pipeline and isolated pair gate.

D-01/D-02/D-03 remain dependencies for later implementation, not decisions closed by these synthetic fixtures. Agent 0 should compare the accepted package base against the intended integration destination and record acceptance after the correction. This review neither merges nor modifies the contract or original Review-1 report.

## Reproduction appendix

Run the following standard-library Python program from a clone containing the exact target commit. It reads that commit even if another branch is checked out. Expected final count: `93/93 independent assertions passed`. The individual case output makes the oracle assumptions visible.

```python
import copy, datetime as dt, json, re, subprocess
SHA = '4c8f77f9ade041bc3a8051758817c9a6aea79adf'
doc = subprocess.check_output(['git','show',SHA+':docs/design/quote-contract.md'], text=True)
F = json.loads(re.search(r'```json\s*(.*?)```', doc, re.S)[1])
I, S, T, E = 'INVALID_QUOTE', 'SOURCE_UNAVAILABLE', 'STALE_QUOTE', 'FEE_UNAVAILABLE'
C, A, K = 'INCOMPARABLE_QUOTES', 'ASSET_MISMATCH', 'QUOTE_TIME_SKEW'
results = []
def check(name, actual, expected):
    results.append((name, actual, expected))
    assert actual == expected, (name, actual, expected)
def uint(x, zero=False):
    return isinstance(x,str) and re.fullmatch(r'(?:0|[1-9][0-9]{0,77})' if zero else r'[1-9][0-9]{0,77}',x) is not None
def stamp(x):
    if not isinstance(x,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z',x): raise ValueError()
    return dt.datetime.strptime(x,'%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=dt.timezone.utc)
NOW = stamp('2026-11-23T00:00:02.000Z')
def fees(r):
    required = {('sim-b','BUY'): {'gas'}, ('sim-a','SELL'): {'base-fee'}}.get((r['sourceId'],r['side']),set())
    costs = r['includedCosts']
    # Duplicate required components cannot establish exactly-once coverage.
    if len(costs)>32: return I
    if {c['costId'] for c in costs} != required or len(costs)!=len(required): return E
    separate = 0
    for c in costs:
        if not uint(c['settlementCostUnits'],True): return I
        if c['treatment']=='EMBEDDED':
            if set(c)!={'costId','settlementCostUnits','treatment'}: return I
        elif c['treatment']=='SEPARATE':
            if not uint(c['units'],True): return I
            if not uint(c['rateNumerator']) or not uint(c['rateDenominator']): return E
            n,d = int(c['rateNumerator']),int(c['rateDenominator'])
            if c['assetId']==r['settlementAssetId']:
                if n!=d: return E
            elif c.get('rateRef')!='sim-rate-1': return E
            cost=(int(c['units'])*n+d-1)//d
            if cost!=int(c['settlementCostUnits']): return E
            separate+=cost
        else: return I
    expected=int(r['grossSettlementUnits'])+(separate if r['side']=='BUY' else -separate)
    return None if expected==int(r['settlementUnits']) else I
REQ=set(F[0]['request'])
def validate(f, now=NOW, connected=True, epoch='e1'):
    q,r=f['request'],f['response']
    if set(q)!=REQ: return I
    if q['mode']!='SIMULATION': return I
    if r.get('status')=='ERROR':
        if set(r)!={'status','requestId','sourceId','code'}: return I
        if any(r.get(k)!=q[k] for k in ['requestId','sourceId']): return I
        return r['code'] if r['code'] in [I,S,T,E] else I
    if set(r)!=REQ|{'status','settlementUnits','grossSettlementUnits','includedCosts','observedAt','connectionEpoch'} or r.get('status')!='OK': return I
    if any(r.get(k)!=v for k,v in q.items()): return I
    for k in ['baseUnits','settlementUnits','grossSettlementUnits']:
        if not uint(r[k]): return I
    for k in ['baseScale','settlementScale']:
        if type(r[k]) is not int or not 0<=r[k]<=38: return I
    try: age=(now-stamp(r['observedAt'])).total_seconds()*1000
    except (ValueError,TypeError): return I
    if age<0 or r['connectionEpoch']!=epoch: return I
    fee=fees(r)
    if fee==I: return I
    if not connected: return S
    if age>=5000: return T
    return fee

def pair(b,s,threshold=300,now=NOW,skew=2000,mapped=True,active='m1',mode='SIMULATION',volume=200):
    errors=[validate(x,now) for x in [b,s]]
    errors=[x for x in errors if x]
    if errors: return min(errors,key=[I,S,T,E].index)
    br,sr=b['response'],s['response']
    if (br['side'],sr['side'])!=('BUY','SELL'): return C
    if (br['sourceId'],sr['sourceId']) not in [('sim-a','sim-b'),('sim-b','sim-a')]: return C
    if any(r['mode']!=mode or r['relationshipId']!='sim-ab' or r['mappingRevision']!=active or r['feePolicyId']!='sim-fees-1' for r in [br,sr]): return C
    if not mapped: return A
    scale=max(br['baseScale'],sr['baseScale'],2)
    units=lambda r,k,sk: int(r[k])*10**(scale-r[sk])
    if units(br,'baseUnits','baseScale')!=units(sr,'baseUnits','baseScale') or units(br,'baseUnits','baseScale')!=volume*10**(scale-2): return C
    if abs((stamp(br['observedAt'])-stamp(sr['observedAt'])).total_seconds()*1000)>skew: return K
    scale=max(br['settlementScale'],sr['settlementScale'])
    spread=units(sr,'settlementUnits','settlementScale')-units(br,'settlementUnits','settlementScale')
    return spread,spread>threshold

def mutate(index, **fields):
    f=copy.deepcopy(F[index]); f['response'].update(fields); return f
for i,f in enumerate(F): check('fixture '+str(i+1),validate(f),None)
check('forward',pair(F[0],F[1]),(325,True))
for threshold,opportunity in [(241,True),(242,False),(243,False)]: check('reverse threshold '+str(threshold),pair(F[2],F[3],threshold),(242,opportunity))
check('gas rounding',(5*1+2-1)//2,3)
check('fixed base SELL',199+1,200)
check('fixed base BUY illustrative',201-1,200)
check('embedded price delta',10300-10245,55)
check('changed response volume',validate(mutate(1,baseUnits='201')),I)
check('independently unequal volumes',pair(F[0],F[4]),C)
for k in sorted(REQ):
    v=F[0]['response'][k]
    check('echo '+k,validate(mutate(0,**{k:v+1 if type(v)==int else v+'x'})),I)
for value in ['0','00','01','-1','+1','1.0','1e2',' 1','1 ','١','9'*79]:
    check('bad quantity '+repr(value),validate(mutate(1,settlementUnits=value)),I)
check('78 digits',uint('9'*78),True)
check('arbitrary precision',int('9'*78)+1,10**78)
check('scaled exact equality',200*10**1,20*10**2)
for v in [-1,39,2.5,True]:
    f=mutate(0,baseScale=v); f['request']['baseScale']=v
    check('invalid scale '+repr(v),validate(f),I)
check('missing fee',validate(mutate(2,includedCosts=[])),E)
f=copy.deepcopy(F[2]); f['response']['includedCosts'][0]['rateDenominator']='0'
check('zero denominator',validate(f),E)
f=copy.deepcopy(F[2]); f['response']['includedCosts']*=2
check('duplicate required fee interpretation',validate(f),E)
f=copy.deepcopy(F[2]); f['response']['includedCosts'][0]['rateRef']='expired'
check('unavailable rate',validate(f),E)
check('wrong final reconciliation',validate(mutate(2,settlementUnits='10004')),I)
check('double embedded deduction',validate(mutate(3,settlementUnits='10190')),I)
f=copy.deepcopy(F[2]); f['response']['includedCosts'][0].update(units='0',settlementCostUnits='0'); f['response']['settlementUnits']='10000'
check('explicit zero fee',validate(f),None)
check('zero proceeds',validate(mutate(3,settlementUnits='0')),I)
for age,expected in [(4999,None),(5000,T)]:
    check('age '+str(age),validate(F[0],stamp('2026-11-23T00:00:00.000Z')+dt.timedelta(milliseconds=age)),expected)
for skew,expected in [(1000,(325,True)),(999,K)]: check('skew '+str(skew),pair(F[0],F[1],skew=skew),expected)
for value in ['2026-11-23T00:00:02.001Z','2026-11-23T00:00:01Z','2026-11-23T00:00:01.000+00:00','2026-02-30T00:00:01.000Z','2026-11-23T00:00:60.000Z','0000-01-01T00:00:00.000Z']:
    check('bad timestamp '+value,validate(mutate(0,observedAt=value)),I)
check('obsolete mappings',pair(F[0],F[1],active='m2'),C)
check('removed base mapping',pair(F[0],F[1],mapped=False),A)
check('removed settlement mapping',pair(F[0],F[1],mapped=False),A)
check('configured volume mismatch',pair(F[0],F[1],volume=201),C)
check('request error before missing mapping',pair(F[0],F[1],mapped=False,now=stamp('2026-11-23T00:00:05.000Z')),T)
check('mapping before skew',pair(F[0],F[1],mapped=False,skew=0),A)
err={'status':'ERROR','requestId':'q2','sourceId':'sim-b','code':S}
check('correlated error',validate(mutate(1,**{} ) | {'response':err}),S)
check('wrong call correlation',validate({'request':F[2]['request'],'response':err}),I)
check('unknown error code',validate({'request':F[1]['request'],'response':dict(err,code='UNKNOWN')}),I)
check('missing correlation',validate({'request':F[1]['request'],'response':{k:v for k,v in err.items() if k!='requestId'}}),I)
check('unrelated valid reverse after q2 error',pair(F[2],F[3],241),(242,True))
check('source down',validate(F[0],connected=False),S)
check('old epoch',validate(F[0],epoch='e2'),I)
check('outage before age',validate(F[0],connected=False,now=stamp('2026-11-23T00:00:05.000Z')),S)
check('stale before missing fee',validate(mutate(2,includedCosts=[]),now=stamp('2026-11-23T00:00:06.000Z')),T)
check('simulation into LIVE decision',pair(F[0],F[1],mode='LIVE'),C)
f=copy.deepcopy(F[1]); f['request']['mode']=f['response']['mode']='LIVE'
check('mixed pair full SIM-stage pipeline',pair(F[0],f),I)
# Stateful recovery predicate only: this is not a concurrency implementation.
def eligible(epoch,t,last,recovery,now):
    return epoch=='e2' and t>=recovery and t>last and 0<=(now-t).total_seconds()*1000<5000
last=stamp('2026-11-23T00:00:01.000Z'); recovery=stamp('2026-11-23T00:00:03.000Z'); now=stamp('2026-11-23T00:00:03.002Z')
for label,epoch,t,expected in [('old generation','e1','03.001',False),('replay','e2','01.000',False),('before recovery','e2','02.999',False),('fresh recovery','e2','03.001',True)]:
    check(label,eligible(epoch,stamp('2026-11-23T00:00:'+t+'Z'),last,recovery,now),expected)
# Lexical wire checks independent of the parsed-fixture validator.
def wire(raw):
    if len(raw.encode('utf8'))>16384: return False
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d: raise ValueError('duplicate')
            d[k]=v
        return d
    try: return isinstance(json.loads(raw,object_pairs_hook=unique),dict)
    except (ValueError,TypeError): return False
check('duplicate JSON keys',wire('{"x":1,"x":2}'),False)
check('payload 16384 bytes',wire('{"x":"'+'a'*16376+'"}'),True)
check('payload 16385 bytes',wire('{"x":"'+'a'*16377+'"}'),False)
for token,expected in [('0',True),('38',True),('-0',False),('2.0',False),('2e0',False),('39',False)]:
    check('lexical scale '+token,bool(re.fullmatch(r'(?:0|[1-9][0-9]*)',token)) and int(token)<=38 if re.fullmatch(r'(?:0|[1-9][0-9]*)',token) else False,expected)
print(str(len(results))+'/'+str(len(results))+' independent assertions passed')
for name,actual,expected in results: print(name+': '+repr(actual))

```
