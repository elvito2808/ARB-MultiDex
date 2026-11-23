# DEX-02-01 / Revision 1 — SaucerSwap V1 market discovery

| Field | Value |
| --- | --- |
| Owner / reviewer / supervisor | S1 / S5 / Agent 0 |
| Base | `agent-governance@9acb21a38c4cacbef8af061ab86a09f06913dec2` |
| Work branch | `dex-02-01-hedera-discovery` |
| Changed file | `docs/research/hedera-saucerswap-v1.md` |
| Result | **READY_FOR_REVIEW** |
| Result reference | [Compare review branch with pinned base](https://github.com/elvito2808/ARB-MultiDex/compare/9acb21a38c4cacbef8af061ab86a09f06913dec2...dex-02-01-hedera-discovery) |

The owner selected this market. The pinned governance limits work to non-operational design and rules out LIVE implementation and real transactions. The checks used public contract metadata and unsigned read-only calls for evidence on 2026-11-23 UTC. The work branch contains research only. The package names S5 as reviewer. The standing responsibility matrix names S2 as the DEX reviewer and S5 as a consultation role. Agent 0 should record how the package-specific assignment applies at handoff. [Governance](../../AGENTS.md), [roles](../agents/roles.md), [responsibilities](../agents/responsibilities.md), [handoff](../agents/handoff.md), [open decisions](../agents/decisions.md).

## Confirmed network, pool and assets

Hedera Mainnet has EVM chain ID **295** (`0x127`). The public relay returned `0x127` for `eth_chainId`, consistent with the Hedera and Hiero documentation. The relay and public Mirror Node were evidence sources for this review. Endpoint selection for the adapter remains open. [Hedera RPC connections](https://docs.hedera.com/evm/tutorials/intermediate/json-rpc-connections), [Hiero relay configuration](https://github.com/hiero-ledger/hiero-json-rpc-relay).

| Entity | Owner-supplied EVM address | Verified identity |
| --- | --- | --- |
| Pair | `0xdB34c1Ef944883f0e5A2fC18B6C1978B088bD31d` | Mainnet contract `0.0.1462797`; official V1 factory lookup returned this EVM address. [Mirror Node contract](https://mainnet-public.mirrornode.hedera.com/api/v1/contracts/0xdb34c1ef944883f0e5a2fc18b6c1978b088bd31d), [HashScan](https://hashscan.io/mainnet/contract/0.0.1462797). |
| WHBAR | `0x0000000000000000000000000000000000163B5a` | `token1`; SaucerSwap Wrapped Hbar, HTS fungible token `0.0.1456986`, symbol `WHBAR`, **8 decimals**. This token address is distinct from the wrapper contract `0.0.1456985`. [Mirror Node token](https://mainnet-public.mirrornode.hedera.com/api/v1/tokens/0.0.1456986), [SaucerSwap deployments](https://docs.saucerswap.finance/developers/contracts). |
| USDC | `0x000000000000000000000000000000000006f89a` | `token0`; Circle native USD Coin, HTS fungible token `0.0.456858`, symbol `USDC`, **6 decimals**. [Mirror Node token](https://mainnet-public.mirrornode.hedera.com/api/v1/tokens/0.0.456858), [Circle](https://www.circle.com/multi-chain-usdc/hedera). |

All supplied addresses passed 20-byte EVM hex syntax and matched the returned values by address bytes. No supplied address was substituted. The pair's `factory()` matched the official V1 factory `0.0.1062784` (`0x0000000000000000000000000000000000103780`). Its `getPair(USDC,WHBAR)` returned the supplied pair. The factory source sorts token addresses, consistent with the observed order. Read the getters and compare the unordered set to the configured assets before assigning `reserve0` and `reserve1`. The printable symbol is display metadata. [Official deployments](https://docs.saucerswap.finance/developers/contracts), [factory lookup guide](https://docs.saucerswap.finance/developers/v1/liquidity/check-if-a-pool-exists), [pinned factory source](https://github.com/saucerswaplabs/saucerswaplabs-core/blob/873d092321ac4872e16f5eb702cef770f9cd790f/contracts/UniswapV2Factory.sol).

Both token addresses are the 0.0 HTS long-zero representation of the numeric token ID. Use the native token endpoint to verify identity and metadata. The ERC-20-compatible `decimals()` getter succeeded for both EVM token addresses and agreed with the Mirror Node. `symbol()` and `balanceOf(address)` are relevant ERC-20-compatible interfaces, with symbols verified here via HTS metadata. The WHBAR token returned 404 through the contract-entity endpoint and resolved through the token endpoint. The adapter needs no wrapper, helper or token-transfer call. The SaucerSwap wrapper security advisory applies to separate wrapping and allowance flows. [Hedera ERC-20 documentation](https://docs.hedera.com/evm/erc-20), [WHBAR advisory](https://www.saucerswap.finance/blog/whbar-contract-securityadvisory).

## Pair read ABI

The read subset below is derived from the official V1 interface pinned at `873d092321ac4872e16f5eb702cef770f9cd790f` and verified against the deployed contract's return data. This is a complete ABI for these three reads. The upstream `abi/` directory supplies a factory artifact. The Pair subset is transcribed from the Pair interface and implementation, with the reserve types independently cross-checked against the SaucerSwap reserve guide. [Pair interface](https://github.com/saucerswaplabs/saucerswaplabs-core/blob/873d092321ac4872e16f5eb702cef770f9cd790f/contracts/interfaces/IUniswapV2Pair.sol), [Pair implementation](https://github.com/saucerswaplabs/saucerswaplabs-core/blob/873d092321ac4872e16f5eb702cef770f9cd790f/contracts/UniswapV2Pair.sol), [official reserve guide](https://docs.saucerswap.finance/developers/v1/liquidity/get-pool-reserves).

```json
[
  {"type":"function","name":"token0","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"address"}]},
  {"type":"function","name":"token1","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"address"}]},
  {"type":"function","name":"getReserves","stateMutability":"view","inputs":[],"outputs":[{"name":"reserve0","type":"uint112"},{"name":"reserve1","type":"uint112"},{"name":"blockTimestampLast","type":"uint32"}]}
]
```

| Function | Selector | Return encoding |
| --- | --- | --- |
| `token0()` | `0x0dfe1681` | One 32-byte word with a right-aligned address |
| `token1()` | `0xd21220a7` | One 32-byte word with a right-aligned address |
| `getReserves()` | `0x0902f1ac` | Three 32-byte words with the ABI widths shown above |
| `factory()` | `0xc45a0155` | One address word, optional startup provenance check |

The Pair uses standard EVM ABI encoding for these reads. Its implementation uses HTS for token association and transfers and creates an HTS LP token. Reserve amounts are cached pool state in the constituent tokens' smallest units. Mirror account balances can differ from those cached reserves. The last word of `getReserves()` is the EVM timestamp in seconds modulo 2³² when the Pair last updated reserves. The Pair source and behavior were cross-checked through the ABI and factory and router calls. Reproducible compilation and deployed bytecode equivalence were not established. Mirror Node returned 16,541 bytes of runtime code with SHA-256 `568ec51702577d2e4b7b64e07bec5a78af8686e99dfea1162fa9a9812a96591a`. This digest identifies the retrieved runtime. [Pair source](https://github.com/saucerswaplabs/saucerswaplabs-core/blob/873d092321ac4872e16f5eb702cef770f9cd790f/contracts/UniswapV2Pair.sol), [Mirror contract record](https://mainnet-public.mirrornode.hedera.com/api/v1/contracts/0.0.1462797).

## Pricing model and fee coverage

SaucerSwap documents a **30 bps (0.30%) V1 swap fee**. The pinned Pair invariant multiplies each post-swap balance by 1000 and subtracts three times the corresponding input. The resulting input multiplier is `997/1000`. The documented split is five-sixths to LPs and one-sixth to the protocol, equivalent to 25 and 5 bps of swap input. The Pair implementation conditionally mints protocol LP liquidity based on growth in the square root of the invariant. Apply the swap fee once. A fee-enabled router or transaction gas has separate economics. [Official V1 fee model](https://docs.saucerswap.finance/protocol/saucerswap-v1), [Pair fee invariant](https://github.com/saucerswaplabs/saucerswaplabs-core/blob/873d092321ac4872e16f5eb702cef770f9cd790f/contracts/UniswapV2Pair.sol), [V1 router deployments](https://docs.saucerswap.finance/developers/contracts).

For this pool let `B = reserve1` (WHBAR), `S = reserve0` (USDC), and `q` be the exact WHBAR size in 8-decimal atomic units. The pool prices a single hop from a coherent state under the constant-product invariant `B × S`. For standard balance changes without transfer fees or excess donated input:

- **SELL exact base in.** USDC output is `floor(997 × q × S / (1000 × B + 997 × q))`.
- **BUY exact base out.** The minimum USDC input allowed by the fee-adjusted invariant is `ceil(1000 × S × q / (997 × (B − q)))`. A conservative router-compatible candidate uses `floor(numerator / denominator) + 1`, which adds one atomic USDC unit in the exact-division case. The recorded router comparison confirms the sample result, where the quotient has a remainder. Verify exact-division behavior against the chosen reference before promising atom-exact router parity. [Official V1 quote methods](https://docs.saucerswap.finance/developers/v1/swap/swap-quote).

Use exact wide integers, validate ABI sizes and ranges, reject zero reserves, nonpositive or unrepresentable volume, BUY `q ≥ B` and SELL outputs rounded down to zero. Convert to display units only after pricing. A normalized reserve ratio is an indicative spot rate. Finite-size amounts include price impact and the pool swap fee. Both token metadata responses showed empty fixed and fractional custom-fee lists at discovery. Revalidate assumptions about token fee configuration and unusual balance behavior before relying on the model. Future network fee estimation, native HBAR-to-USDC conversion, any additional router fee and settlement-wide cost policy remain open under D-03. A pool-only amount cannot stand in for a final cost-inclusive opportunity quote. [DEX requirements §§5–9](../../DEX_Module_Requirements_v1.0.md), [open decisions](../agents/decisions.md).

## Read paths, timestamps and failure handling

| Read path | Confirmed capability | Adapter considerations |
| --- | --- | --- |
| Hedera JSON-RPC relay | `eth_chainId`, `eth_getBlockByNumber` and `eth_call` at a specified block succeeded. | Verify chain ID and block before using state. Hex block numbers and integer-second timestamps come through the Ethereum-shaped API. Relay capabilities, throttles, caches and archive coverage can vary. [Hedera RPC guide](https://docs.hedera.com/evm/tutorials/intermediate/json-rpc-connections), [Hiero relay](https://github.com/hiero-ledger/hiero-json-rpc-relay). |
| Mirror Node REST | Block number and consensus timestamp range, entity and HTS metadata, and unsigned `POST /api/v1/contracts/call` at a specified block with `estimate:false`. | REST uses the configured network host and chain context. Bind it to Mainnet configuration and corroborate the factory, pair and block identity. Its block record supplied nanosecond timestamps and a longer record hash. The matched relay block returned the same height and the first 32 bytes of the Mirror hash in this sample. Confirm normalization before treating cross-interface hashes as interchangeable. [Mirror REST OpenAPI](https://mainnet.mirrornode.hedera.com/api/v1/docs/), [Mirror Node architecture](https://docs.hedera.com/operators/mirror-node/architecture). |

Treat three times separately. The sample block's consensus range was `1795474157.225278104` to `1795474158.266696104` UTC epoch seconds. The relay block timestamp was `1795474157` seconds. The Pair's last reserve update was `1795474043` seconds, about 114 seconds earlier. An unchanged reserve tuple can be confirmed at a newer block. A repeated read at the same block does not make its state newer. Store the pinned snapshot block and its proven chain-derived time, the reserve-update time and local receipt time. Adopt a documented snapshot-time convention for freshness, distinguish it from optional pool inactivity checks, and align it with D-03 before integrating with quote consumers.

Connections should handle transport, bounded timeout and retry, endpoint identification and recovery. DEX should verify market and ABI, ensure block-consistent state and apply local freshness policy before publishing. Read a block identity first and pin dependent state calls. Use bounded backoff for timeouts, HTTP 429 or transient server errors. Treat unsupported historical height, wrong chain, revert, malformed or empty ABI, stale block and regressed block as separate failure cases in diagnostics. HTTP success can still contain an RPC error. Fail over only after checking the fallback's network and freshness, and retry the snapshot as a unit if the previous snapshot cannot be reproduced. A relay may depend on similar Mirror Node infrastructure, so the observed parity does not establish independent failure domains or a lag SLA. Recovery needs a fresh valid snapshot, and failures should affect only markets that depend on the failed data. [Connections §§4–5 and 8–9](../../Connections_Module_Requirements_v1.0.md), [DEX §§7–9](../../DEX_Module_Requirements_v1.0.md), [Security and Testing §§8, 15, 26–27](../../Project_Security_and_Testing_Requirements_v1.0.md).

## Checks and evidence

Block `102699974` (`0x61f13c6`) is the pinned discovery sample. The amounts are historical read-only evidence. [Mirror Block](https://mainnet-public.mirrornode.hedera.com/api/v1/blocks/102699974).

| Check | Expected | Observed |
| --- | --- | --- |
| EVM syntax and identity | Three 20-byte hex values; pair and token addresses match configured bytes | Passed all three; EIP-55 checksum validation was outside the syntax check. |
| Factory provenance | Pair points to official V1 factory; factory returns pair for supplied assets | Passed both lookups. |
| Token order and metadata | Unordered match and agreed token scales | USDC first, scale 6; WHBAR second, scale 8. Token records and `decimals()` agreed. |
| Reserve tuple | Three correctly sized words; same bytes on both paths at the pinned block | `(198803970955, 379049637493682, 1795474043)` on both. Display liquidity 198,803.970955 USDC and 3,790,496.37493682 WHBAR. |
| Pricing | One WHBAR BUY and SELL from integer math agree with corresponding V1 router view calls | BUY input `52606` (0.052606 USDC) and SELL output `52290` (0.052290 USDC), exact matches. |
| Scope and omissions | Public unsigned reads and local assertions only | No wallet, signing, on-chain writes or integration suite. Endpoint outage, throttling, archive depth, delayed ingestion, exact-division rounding and full source-to-bytecode reproduction remain untested. |

The checks used `curl` for capture, `jq` for record inspection and a scratch integer assertion script for decoding and comparison. Equivalent request templates follow. Router calldata appends ABI words for `q=100000000`, dynamic array offset `64`, array length `2`, then the two left-padded path addresses.

```sh
mirror=https://mainnet-public.mirrornode.hedera.com/api/v1
relay=https://mainnet.hashio.io/api
pair=0xdb34c1ef944883f0e5a2fc18b6c1978b088bd31d
curl -fsS "$mirror/contracts/$pair"
curl -fsS "$mirror/tokens/0.0.1456986"
curl -fsS "$mirror/tokens/0.0.456858"
curl -fsS "$mirror/blocks/102699974"

call() {
  curl -fsS "$mirror/contracts/call" -H 'Content-Type: application/json' \
    --data "{\"to\":\"$1\",\"data\":\"$2\",\"block\":\"102699974\",\"estimate\":false}"
}
usdc=0x000000000000000000000000000000000006f89a
whbar=0x0000000000000000000000000000000000163b5a
factory=0x0000000000000000000000000000000000103780
router=0x00000000000000000000000000000000002e7a5d
for selector in 0x0dfe1681 0xd21220a7 0x0902f1ac 0xc45a0155; do
  call "$pair" "$selector"
done
call "$whbar" 0x313ce567
call "$usdc" 0x313ce567
usdc_word=$(printf '%064x' 456858)
whbar_word=$(printf '%064x' 1456986)
quote_args=$(printf '%064x%064x%064x' 100000000 64 2)
call "$factory" "0xe6a43905${usdc_word}${whbar_word}"
call "$router" "0x1f00ca74${quote_args}${usdc_word}${whbar_word}"
call "$router" "0xd06ca61f${quote_args}${whbar_word}${usdc_word}"

curl -fsS "$relay" -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'
curl -fsS "$relay" -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":3,"method":"eth_getBlockByNumber","params":["0x61f13c6",false]}'
curl -fsS "$relay" -H 'Content-Type: application/json' \
  --data "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"eth_call\",\"params\":[{\"to\":\"$pair\",\"data\":\"0x0902f1ac\"},\"0x61f13c6\"]}"
```

**Next action.** S5 reviews the pinned evidence, read-only boundary and failure cases. Agent 0 records the role handoff and settles the endpoint, freshness and fee coverage decisions with the appropriate owners before authorizing adapter implementation.
