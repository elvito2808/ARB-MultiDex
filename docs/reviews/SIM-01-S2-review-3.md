# SIM-01 / S2 Review-3

**Verdict: ACCEPTED at the contract design level.** B1 is resolved. Agent 0 owns the integration and final acceptance decision.

| Field | Value |
| --- | --- |
| Task / review / date | SIM-01 / S2 Review-3 / 2026-11-23 UTC |
| Supervisor / implementation owner / reviewer | Agent 0 / S1 Market Data & Connectivity / S2 Opportunity Detection |
| Consultant | S5, consultation role. No separate consultation or attestation was performed. |
| Repository | elvito2808/ARB-MultiDex |
| Governance | `agent-governance` at `9acb21a38c4cacbef8af061ab86a09f06913dec2` |
| Implementation | `sim-01-quote-contract-r3` at `8a40802945a02aa78fcbdee86d96493fd73bad31` |
| Revision 2 base | `4c8f77f9ade041bc3a8051758817c9a6aea79adf` |
| Review branch and parent | `sim-01-s2-review-3`, based directly on the implementation SHA above |
| Authorized file | `docs/reviews/SIM-01-S2-review-3.md` |

## Scope and provenance

Read the pinned [governance rules](https://github.com/elvito2808/ARB-MultiDex/tree/9acb21a38c4cacbef8af061ab86a09f06913dec2/docs/agents) and `AGENTS.md`, the [Review-2 report](https://github.com/elvito2808/ARB-MultiDex/blob/523715e06f6a01933895aae65e896d32e4cfa48e/docs/reviews/SIM-01-S2-review-2.md), and the [Revision 3 contract](https://github.com/elvito2808/ARB-MultiDex/blob/8a40802945a02aa78fcbdee86d96493fd73bad31/docs/design/quote-contract.md). All contract line numbers refer to that implementation SHA. The base-to-target diff is confined to the handoff metadata, mode examples, and attribution of the earlier test claim. The executable fixture inputs and verbatim normative body were checked against Revision 2. Both remote governance and implementation heads matched their pins at review start.

Cross-checked the requirements at `99180c4afda61696cbfbc067014205208e41eb31`. Opportunity §§4–9 require matching assets, size and freshness, independent directions, final-amount profit without a second fee deduction and a strict threshold. DEX §§6–9 and KuCoin §§3–10 require fixed-volume, cost-aware BUY and SELL quotes, explicit market context and valid reuse. Connections §§5 and 9 keep managed connectivity and raw-data validity distinct from final quote validity and prevent stale data from being passed off as fresh. Security and Testing §§8 and 15–17 require same-mode legs, invalid/stale quote rejection and independent opportunity evaluation.

This review was executed in the same workspace and assistant session as the S1 handoff. The S2 verdict uses a separate checker derived from the published Review-2 appendix and direct Git-object inspection. The S1 scratch checker is supporting evidence with its own result below.

## Review-2 finding resolution matrix

| Original finding | Review-3 disposition and evidence |
| --- | --- |
| 1. Fee coverage | Remains resolved. Lines 32–36 and 52–55 preserve exactly-once required cost coverage, explicit zero, per-component conversion and `FEE_UNAVAILABLE` for unavailable conversions. BUY adds separate fees, SELL subtracts them, and the consumer subtracts final amounts. Fixed-base BUY and SELL preserve net receipt and total debit respectively. |
| 2. Quantity and binding | Remains resolved. Lines 20 and 26–28 preserve exact 12-field echo, bounded wire format, ASCII minor-unit strings and registered scales. The checker distinguishes an altered q2 response volume from independently valid unequal q1/q5 volumes. |
| 3. Market identity | Remains resolved. Lines 22 and 59–64 preserve immutable market scope, local asset identities and explicit versioned mappings. Echo mismatch is request-level. Obsolete active revision, disallowed pair context and missing asset equivalence are checked at their respective pair gates. |
| 4. Freshness and cache | Remains resolved at design level. Lines 40–44 retain fixed UTC observations, age 4999/5000 ms and skew 1000/999 ms boundary examples, mode/market generation identity, invalidation of in-flight and cached old data, persisted watermark, and current-generation recovery with a strictly newer snapshot. |
| 5. Error contract | Resolved. Lines 48–66 preserve correlated errors, request precedence `INVALID_QUOTE` > `SOURCE_UNAVAILABLE` > `STALE_QUOTE` > `FEE_UNAVAILABLE`, BUY tie-breaking and direction-local pair failure. B1-A/B now agree with this precedence. |
| 6. Simulation and consumer boundary | Resolved. Lines 26, 61, 66 and 296–311 preserve SIMULATION-only production, label the direct mixed-mode test as hypothetical, and require a mode-keyed cache miss for a LIVE decision. |

## B1 results

| Case | Independent result |
| --- | --- |
| A — Full pipeline, matching LIVE echo | q1 validates. All 12 q2 echoes match and both q2 modes are LIVE. q2 fails request validation with `INVALID_QUOTE`; pair-gate calls remain zero. |
| B — Full pipeline, response-only mismatch | q1 validates. q2 requests SIMULATION and only the response mode changes to LIVE. q2 fails echo validation with `INVALID_QUOTE`; pair-gate calls remain zero. |
| C — Isolated consumer pair gate | Direct invocation with hypothetical SIMULATION and LIVE quotes returns `INCOMPARABLE_QUOTES`. The same LIVE leg fails producer request validation, so the mixed combination is unreachable through the current pipeline. |
| D — Mode-keyed cache | A SIMULATION control lookup hits. A LIVE lookup against the same cache misses; the SIMULATION record independently fails LIVE eligibility and the modeled decision produces no opportunity. |

The model also passed request-before-pair-error checks and direction isolation checks. The contract makes a q2-only error local to that request; an actual sim-b disconnect affects every sim-b leg in that mode, including reverse BUY. Independent work with healthy inputs can continue. The scheduling and correlation invariants were reviewed in prose in addition to the model predicates.

## Integer checks

| Check | Result |
| --- | --- |
| Forward at threshold 300 | `10450 - 10125 = 325` settlement minor units; opportunity |
| Reverse BUY | `ceil(5 × 1 / 2) = 3` minor units; `10000 + 3 = 10003` |
| Reverse SELL | `199 + 1 = 200` base minor units; the `10300 - 10245 = 55` fee effect is already embedded |
| Reverse spread and threshold | `10245 - 10003 = 242` minor units. Threshold 241 qualifies; 242 and 243 do not. |

The consumer uses the final amounts, so neither 3 nor 55 is deducted again. The synthetic pricing values are fixture premises. The BUY fixed-base invariant was checked in prose and the illustrative arithmetic `201 - 1 = 200`; the contract supplies a cost-bearing fixed-base SELL fixture.

## Execution and limits

- Review-2’s published checker passed **93/93** on the pinned Revision 2 base. A separate pinned Revision 3 execution passed **115/115** checks (93 inherited and 22 additional). The model uses standard-library Python, fixed UTC times and arbitrary-precision integers.
- Located S1’s exact `/workspace/work/revision3.py` and its original `revision3.log` in this workspace. Inspected the checker and its inventory: the published 93 checks plus eight checks for matching LIVE modes, request precedence over mapping, response-only mismatch, the isolated gate, and four cache controls/results. Reran that exact source with the pinned implementation as its working tree and compared the entire emitted inventory with the original log. The output matched byte for byte. The source SHA-256 is `91a98a8c21f4d885e576d7596672c89c56b9e2cf27b1e6fe0fef7c254ff6d629`. The 101/101 result is a rerun of S1’s own model. The S2 checks below additionally instrument gate invocation and confirm every echoed field in case A.
- S1’s original 37/37 claim remains unverified. The exact checker and assertion inventory for that run were unavailable.
- Model coverage uses synthetic registries, repeated fixture data and a sequential recovery predicate. Neither the cache model nor the freshness checks prove runtime cache isolation, in-flight invalidation, concurrent recovery, clock synchronization or adapter correctness. Quote parsing, error attribution and unrelated-worker scheduling are only partially modeled. No runtime, provider, wallet or financial operations were exercised.
- No blocking finding remains. The evidence limitations above are non-blocking for this design review. D-01, D-02 and D-03 remain open.

## Reproduction

Run the Python block below from a clone containing the three pinned commits. It executes Review-2’s published checker verbatim against its original base and then changes only its input SHA for the inherited Revision 3 regressions. The additions test the contract diff, validation stages and mode cache separately. Each added assertion emits its name and result.

```python
import copy
import re
import subprocess

BASE = '4c8f77f9ade041bc3a8051758817c9a6aea79adf'
TARGET = '8a40802945a02aa78fcbdee86d96493fd73bad31'
REVIEW2 = '523715e06f6a01933895aae65e896d32e4cfa48e'
PATH = 'docs/design/quote-contract.md'

def git(*args):
    return subprocess.check_output(['git', *args], text=True)

source = re.search(r"```python\n([\s\S]*?)\n```", git(
    'show', REVIEW2 + ':docs/reviews/SIM-01-S2-review-2.md'))[1]
exec(compile(source, '<Review-2 base>', 'exec'), {})
model = {}
exec(compile(source.replace(BASE, TARGET, 1), '<Review-2 checks on R3>', 'exec'), model)

check, results = model['check'], model['results']
q1, q2, q3, q4, _ = model['F']
validate, review2_pair = model['validate'], model['pair']
invalid, incomparable = model['I'], model['C']

def contract_body(sha):
    return git('show', sha + ':' + PATH).split('## درخواست، پاسخ و هویت بازار', 1)[1].split(
        '| تغییر mode', 1)[0].split('### مسیر کامل اعتبارسنجی درخواست و پاسخ', 1)[0].rstrip()

check('exact Revision 3 parent', git('rev-parse', TARGET + '^').strip(), BASE)
check('only contract changed', git('diff', '--name-only', BASE, TARGET).splitlines(), [PATH])
check('verbatim normative body, fixtures and non-mode examples', contract_body(TARGET), contract_body(BASE))

gate_calls = []

def gate(a, b, decision='SIMULATION'):
    gate_calls.append((a['requestId'], b['requestId']))
    if a['mode'] != b['mode'] or a['mode'] != decision:
        return incomparable
    return 'mode-eligible'

def pipeline(a, b):
    errors = [(fixture['request']['requestId'], error)
              for fixture in (a, b) if (error := validate(fixture)) is not None]
    return errors if errors else gate(a['response'], b['response'])

check('valid pipeline calls the gate', pipeline(q1, q2), 'mode-eligible')
check('valid gate control trace', tuple(gate_calls), (('q1', 'q2'),))
gate_calls.clear()

live = copy.deepcopy(q2)
live['request']['mode'] = live['response']['mode'] = 'LIVE'
check('A: exact LIVE echo', {k: live['response'][k] for k in live['request']}, live['request'])
check('A: rejects q2 at request stage', pipeline(q1, live), [('q2', invalid)])
check('A: gate bypass', tuple(gate_calls), ())

mismatch = copy.deepcopy(q2)
mismatch['response']['mode'] = 'LIVE'
changed = [k for k in q2['response'] if q2['response'][k] != mismatch['response'][k]]
check('B: only response mode changes', (mismatch['request'], changed), (q2['request'], ['mode']))
check('B: rejects q2 at echo stage', pipeline(q1, mismatch), [('q2', invalid)])
check('B: gate bypass', tuple(gate_calls), ())

check('C: hypothetical direct pair', gate(q1['response'], live['response']), incomparable)
check('C: gate was entered directly', tuple(gate_calls), (('q1', 'q2'),))
check('C: hypothetical LIVE leg fails producer validation', validate(live), invalid)

key_fields = ('mode', 'relationshipId', 'mappingRevision', 'sourceId', 'side', 'baseAssetId',
              'settlementAssetId', 'baseScale', 'settlementScale', 'baseUnits',
              'feePolicyId', 'connectionEpoch')
record = q1['response']

def key(quote, decision):
    return tuple(decision if field == 'mode' else quote[field] for field in key_fields)

cache = {key(record, record['mode']): record}

def lookup(decision):
    entry = cache.get(key(record, decision))
    return entry, entry is not None and entry['mode'] == decision

def opportunity(decision):
    entry, eligible = lookup(decision)
    if eligible:
        return review2_pair({'request': q1['request'], 'response': entry}, q2)[1]
    return False

check('D: SIMULATION control lookup', lookup('SIMULATION'), (record, True))
check('D: SIMULATION control opportunity', opportunity('SIMULATION'), True)
check('D: LIVE miss', lookup('LIVE'), (None, False))
check('D: stored quote ineligible for LIVE', record['mode'] == 'LIVE', False)
check('D: no LIVE opportunity', opportunity('LIVE'), False)

check('fee reconciliation prevents a second separate BUY charge',
      validate(model['mutate'](2, settlementUnits='10006')), invalid)
check('request precedence over pair mapping', review2_pair(q1, live, mapped=False), invalid)
check('request-local forward rejection leaves reverse eligible',
      (pipeline(q1, mismatch), review2_pair(q3, q4, 241)), ([('q2', invalid)], (242, True)))

for name, actual, _ in results[93:]:
    print(name + ': ' + ('unchanged' if name.startswith('verbatim') else repr(actual)))
print(f'{len(results)}/{len(results)} Review-3 model assertions passed')
```

## Handoff to Agent 0

S2 recommends accepting the reviewed design at `8a40802945a02aa78fcbdee86d96493fd73bad31`. Use the review commit that introduces this report as the evidence reference and compare the reviewed SHA with the intended integration destination before recording final acceptance.
