# DEX-02-01 / S5-Evidence-Review-1

| Field | Value |
| --- | --- |
| Owner / reviewer / supervisor | S1 / S5 / Agent 0 |
| Governance | `agent-governance@9acb21a38c4cacbef8af061ab86a09f06913dec2` |
| Target | `dex-02-01-hedera-discovery@38be85dd629ca55e34dd165440e169e0c95215a7` |
| Review branch | `dex-02-01-s5-evidence-review-1` |
| Verdict | **ACCEPTED for non-operational discovery evidence at the exact target SHA** |

Agent 0 owns integration and records the package-specific S5 evidence assignment alongside the standing S2 DEX review role. Endpoint selection, freshness and cost policy remain open. The verdict covers the documented sample and read-only boundary. Adapter integration and operational readiness are outside the acceptance scope.

## Evidence examined

- Read the exact research blob (`ffc4b933cdc029af9dd891888dc14cc48ec5a34f`) and the pinned governance, including roles, responsibilities, handoff and open decisions. GitHub’s comparison showed two target commits ahead of governance and one changed path, the research document. [Research at target](https://github.com/elvito2808/ARB-MultiDex/blob/38be85dd629ca55e34dd165440e169e0c95215a7/docs/research/hedera-saucerswap-v1.md) · [Governance comparison](https://github.com/elvito2808/ARB-MultiDex/compare/9acb21a38c4cacbef8af061ab86a09f06913dec2...38be85dd629ca55e34dd165440e169e0c95215a7)
- Replayed all 16 requests in the document with bounded curl timeouts. Independently parsed the JSON and ABI words, compared both reserve returns byte for byte, hashed the decoded runtime bytes and recomputed both prices with Python integers. Also repeated the WHBAR contract-entity lookup, which returned HTTP 404.
- Fetched the Pair interface, Pair implementation and factory at upstream SHA `873d092321ac4872e16f5eb702cef770f9cd790f`. The interface confirms the view getters and reserve widths, the implementation confirms cached reserves, modulo-2³² timestamp and the fee-adjusted invariant, and the factory confirms token sorting and pair registration. The deployment list corroborates the factory `0.0.1062784`, wrapper contract `0.0.1456985` and quoted router `0.0.3045981`. Circle corroborates native USDC `0.0.456858`. [Pinned sources](https://github.com/saucerswaplabs/saucerswaplabs-core/tree/873d092321ac4872e16f5eb702cef770f9cd790f/contracts) · [Deployments](https://docs.saucerswap.finance/developers/contracts) · [Circle](https://www.circle.com/multi-chain-usdc/hedera)

## Read-only and security boundary

The documented commands and reviewer replay used public Mirror REST GETs, Mirror `POST /api/v1/contracts/call` with `estimate:false`, and relay `eth_chainId`, `eth_getBlockByNumber` and `eth_call`. The POST bodies carry query calldata and block selectors for simulations. They contain no transaction envelope or signature and submit no consensus transaction. The requests succeeded without a wallet, private key, API credential or account provisioning. The replay performed no signing, transfer, approval, wrapping or state-changing on-chain operation. S1 reports the same boundary. Review of the commands corroborates that claim for the documented requests. The repository does not contain an original execution trace that could establish everything S1 ran.

The exact research file contains public endpoints, public addresses, token and contract IDs, selectors and hashes. No secrets, authorization headers, mnemonic phrases or sensitive credentials were found. Future read clients should exercise a method and destination allowlist in tests and verify that failures never invoke signing or transaction submission. A future allowlist test should also verify that diagnostics omit authorization headers and secrets if endpoint configuration later permits them.

## Reproduced results and limitations

| Check | Independent result | Scope |
| --- | --- | --- |
| Network and block | Relay chain ID `0x127`. Both paths reported height `102699974` (`0x61f13c6`). Relay time `1795474157`. Mirror consensus range `1795474157.225278104`–`1795474158.266696104`. Relay hash `0xa4443f07befbb01955289c23efd4e8de5796877e6fb36f3d25fc028aae5550aa` matched the first 32 bytes of the Mirror hash. | One observed hash-prefix match and one historical height. Cross-height normalization, archive depth and upstream independence are unverified. |
| Pair and assets | Mirror returned pair `0.0.1462797` at `0xdb34c1ef944883f0e5a2fc18b6c1978b088bd31d`. Pair factory and factory lookup agreed with the configured market. `token0` was USDC `0.0.456858`, 6 decimals, and `token1` was WHBAR `0.0.1456986`, 8 decimals. HTS metadata and pinned `decimals()` agreed. Both metadata responses had empty fixed and fractional fee lists. | Identity uses addresses, network and provenance. Symbols are display metadata. Entity GET responses reflect the replay-time view. Fee metadata at the sample height and behavior for other tokens are unverified. |
| Reserves | Both endpoints returned the same 96 bytes, decoding to `(198803970955, 379049637493682, 1795474043)`. The last reserve update preceded the relay block time by 114 seconds. | One coherent observed sample. Block time, last reserve update and local receipt time carry different meanings. A second read of the same height supplies no newer state. |
| Runtime bytes | Mirror returned 16,541 decoded bytes with SHA-256 `568ec51702577d2e4b7b64e07bec5a78af8686e99dfea1162fa9a9812a96591a`. | The digest fingerprints the retrieved runtime. Source-to-deployment equivalence and block-specific code provenance were unverified. |
| One WHBAR quotes | With `q=100000000`, the BUY numerator divided by its denominator gives quotient `52605` and remainder `315878404423814830`. Ceiling and floor-plus-one both give `52606` USDC atoms. SELL integer division gives `52290` atoms. The pinned router returned arrays `[52606,100000000]` and `[100000000,52290]` for the two paths. | One size per direction on one pool. The result covers pool pricing under the documented balance assumptions. Network fees, other router charges, exact-division router parity and settlement economics remain unverified. |

The published request templates are sufficient to regenerate the sample while the historical height remains served. The target has no retained response corpus or original scratch assertion script. It provides enough data to recompute the figures offline, with independent response provenance dependent on live replay. The commands print responses and leave JSON-RPC error checks, ABI validation and comparisons to the reader. Positive integer assertions over a recorded tuple establish arithmetic for that fixture. They provide no coverage of transport, ABI rejection, endpoint recovery, quote publication or deployment equivalence. The research explicitly lists major untested claims in the evidence table and qualifies timestamp and provider assumptions in the failure-handling section.

## Missing negative and recovery tests

| Gap | Expected evidence before adapter acceptance |
| --- | --- |
| Identity, chain and snapshot | Reject wrong chain or Mirror network, malformed addresses, wrong factory, mismatched or duplicate tokens, inconsistent decimals, and block/response mismatches. Exercise reversed configured asset order to ensure reserves map by verified identity. If a read path changes during a snapshot, revalidate and recover a complete coherent snapshot before publication. |
| Transport and ABI | Exercise timeout, disconnection, HTTP 429 and 5xx, HTTP 200 with RPC error, unknown historical block, EVM revert, missing result, malformed hex, short or trailing words, nonzero high bits for narrow ABI types, bad address padding, and bad router array offset or length. Confirm structured diagnostics and suppression of the affected quote. |
| Arithmetic and rounding | Reject zero reserves, zero/negative or nonrepresentable volume, BUY at or above reserve, and SELL rounded to zero. Test the just-below-reserve and wide-integer boundaries and exact division. For example, a synthetic BUY with `S=1, B=1997, q=997` has mathematical input 1 and floor-plus-one input 2. Establish the intended router parity rule separately. |
| Lag, outage and recovery | Inject stale, duplicate-height and regressed snapshots, delayed ingestion, retry exhaustion, failure mid-snapshot, primary-only outage, and shared outage. Check bounded retries, lagging fallback rejection, per-market isolation, continued stale-quote suppression and resumption only after a fresh coherent snapshot. Test timestamp boundary behavior under the chosen convention and oldest supported height. |
| Read-only enforcement | Assert that request construction permits only approved read methods and payloads, rejects signing/submission calls, and keeps error and retry paths inside that boundary. Use mocked faults for negative and recovery coverage. |

## Handoff to Agent 0

No blocking security or evidence-integrity defect was found for this historical sample. The principal evidence gaps are the absence of retained S1 machine-verifiable artifacts, source-to-runtime equivalence, and multi-height and failure-path coverage. Agent 0 can record acceptance of the discovery evidence at the exact target SHA and route the test gaps to the relevant future implementation packages. The standing S2 review, product decisions and integration disposition remain with their assigned owners.
