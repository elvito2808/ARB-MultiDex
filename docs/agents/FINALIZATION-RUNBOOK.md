# ARB-MultiDex — بسته واحد دستورهای باقی‌مانده برای Codex
**شناسه:** FINALIZATION-2026-11-24 / Revision 1
**مالک:** Project Owner | **هماهنگ‌کننده و پذیرنده:** Agent 0 (ChatGPT)
**مخزن:** https://github.com/elvito2808/ARB-MultiDex
**هدف:** تکمیل ربات قابل پیکربندی با **داده واقعی بازار در هر دو حالت**، جداسازی اجرای فرضی از ارسال سفارش، امکان اتصال امن کیف پول و KuCoin، تست مستقل ماژول‌ها و تحویل نهایی قابل بازتولید.

> این یک **Runbook ماندگار و منبع دستور اجرایی** است، نه گزارش اجرا. وجود نقش S1–S5 یا نوشتن این فایل یعنی اجرا نشده است. AMS v0.6.0 در این پروژه نقش‌ها را خودکار اجرا نمی‌کند؛ Codex مأموریت‌ها را طبق Manual Handoff پیاده می‌کند؛ Agent 0 شواهد، SHA، CI و ادغام را تأیید می‌کند.

## 0) خط مبنا و قوانین مشترک همه مراحل

- مبنای Governance هنگام انتشار این سند: \`agent-governance@cfc4974eafdefabc82cb28262ddcf73f24dee3fc\` (این سند پس از commit به SHA جدید منتقل می‌شود؛ در هر مرحله HEAD را دوباره بخوان).
- مبنای Integration در زمان برنامه‌ریزی: \`mvp-python@618137014f6f41d82377b71e1ff9b5abd4337a51\` (S1–S4 MOCK/SIMULATION پذیرفته‌شده؛ CI موفق).
- تحویل جدید S1: **PR #19**، \`mvp-02-s1-real-market-runtime@ec116f57c6f3950ff471549e24ce7bed7377af54\`، **OPEN / UNMERGED**، CI موفق (350 test, 56 subtests). هیچ پذیرش مستقل S2 یا Merge قطعی برای آن هنوز فرض نشود.
- شاخهٔ آماده S4: \`mvp-02-secure-runtime-config\` فعلاً در SHA ادغام قبلی قرار دارد؛ پس از پذیرش و Merge S1 باید بدون Force-Push روی مبنای جدید همگام شود.
- اسناد لازم در هر مرحله: \`AGENTS.md\`، \`docs/agents/roles.md\`، \`docs/agents/responsibilities.md\`، \`docs/agents/handoff.md\`، \`docs/agents/decisions.md\` در HEAD فعلی \`agent-governance\`؛ \`docs/design/quote-contract.md\`، همه اسناد ماژولی و \`Execution_and_Balance_Management_Requirements_v1.0.md\` + \`v1.1.md\` در HEAD شاخه هدف.
- **در شروع هر مرحله:** refها، SHAهای قبلی، وضعیت Issue/PR و CI را مجدداً از GitHub بخوان، تفاوت‌ها را گزارش کن، روی شاخه جداگانه از SHA قطعی شروع کن؛ هیچ مبدأ یا گزارش قدیمی را به‌جای بررسی GitHub نپذیر.
- هر مرحله: **TASK-ID/Revision، مالک پیاده‌سازی، بازبین مستقل، هدف، SHA مبنا، مسیرهای مجاز، خارج از دامنه، آزمون‌ها، PR، کامیت و گزارش** را حفظ کند. نقش بازبین نباید خودش همان تغییر را تأیید کند. اصلاحات با **همان TASK-ID و Revision افزایشی** ثبت شوند.
- **محدوده محصول:** یک رابطهٔ تأییدشده Hedera Mainnet chainId 295 / SaucerSwap V1 / WHBAR-USDC با Pool \`0xdB34c1Ef944883f0e5A2fC18B6C1978B088bD31d\`، KuCoin \`HBAR-USDT\`؛ حجم پایه 250 HBAR، حداقل سود خالص **>= 0.15 USDT** (150000 واحد USDT با ۶ رقم اعشار)، حداکثر سه Worker؛ مسیر DEX first. نام‌های دیگر در اسناد ممکن است فقط مثال باشند.
- نگاشت WHBAR↔HBAR و USDC↔USDT 1:1 فقط سیاست صریح مقایسهٔ مالک برای رابطه مرجع است؛ برابری پایدار در بازار واقعی را تضمین نکن. هزینه، نقدینگی، پیگیری موجودی و اختلاف ارزش واقعی باید معتبر باشد.
- تصمیم قطعی مالک: **داده‌های DEX و KuCoin برای هر دو مقدار \`simulation_enabled\` واقعی باشند**. \`true\` فقط ارسال دو معامله را شبیه‌سازی می‌کند؛ \`false\` مسیر واقعی ارسال هر دو معامله را انتخاب می‌کند. MOCK فقط fixture آفلاین جداگانه است. دو سمت یک آربیتراژ هرگز حالت متفاوت ندارند.
- **دامنه اختیار:** درخواست کاربر مجوز **ساخت کد و آماده‌سازی LIVE** است؛ **مجوز استفاده از سرمایه، کلید واقعی، امضا، ارسال سفارش/تراکنش/Approval واقعی یا اجرای پایلوت با وجوه واقعی نیست**. هر اقدام مالی LIVE واقعی و تصمیم حساس محصول نیازمند تأیید صریح مالک است. هیچ ایجنتی از اعتبارنامه واقعی برای تست خودکار استفاده نکند.
- هیچ کلید خصوصی، Seed Phrase، KuCoin Secret/Passphrase و URL حاوی credential در GitHub، PR، Issue، CI، log، خروجی Codex یا چت ثبت نکن.

## 1) وضعیت کنونی بازار و گیت‌های حقیقت‌سنجی

PR #19 بر اساس گزارش S1، دو منبع را واقعاً خوانده است؛ **هیچ یک در آن مشاهده Quote واجد شرایط نداشت**:
- SaucerSwap در block 102737061، عمر مشاهده 9.581 ثانیه بود و حد \`max_age_ms=5000\` را رد کرد؛ قیمت‌های gross برای 250 WHBAR: BUY 12.534720 USDC، SELL 12.458022 USDC. دادهٔ نمونهٔ 2026-11-24، نه قیمت کنونی و نه فرصت قابل معامله.
- KuCoin در 2026-11-24 حوالی 21:23 UTC، bid 0.05011 و ask 0.05014 USDT/HBAR با عمق کافی گزارش کرد، ولی هزینهٔ settlement در example \`null\` بود؛ \`FEE_UNAVAILABLE\`.
- \`config/markets.example.yaml\` در PR #19: \`minimum_net_profit_units: 0\`، \`one_to_one_settlements: false\`، \`gas_bound: null\`، \`gas_rate_source: null\`، \`settlement_fee_bps: null\`. این‌ها intentionally fail-closed/example هستند و با سناریوی مالک 0.15 و mapping تأییدشده باید توسط S4 در تنظیمات عملیاتی **صریح** و بدون جعل شاهد منطبق شوند. نرخ/منبع Gas قابل اعتماد و تازگی واقعی هنوز باز است.
- برای کارمزد KuCoin 10bps، fee tier و معادل تسویهٔ دارایی، اسپرد، تأیید spender/allowance، gas bound و slippage سقف، ارائه‌دهنده و ریسک عملیاتی: پیش‌فرض آزمایشی را خودکار به واقعیت حساب تعمیم نده. مواردی که تصمیم مالک می‌خواهند در \`docs/agents/decisions.md\` یا گزارش blocker ثبت شوند.

## 2) برنامه اجرا؛ به‌ترتیب وابستگی و **نه هم‌زمان روی فایل مشترک**

### STAGE A — S1/S2-REVIEW-19 / Revision 1
**مسئول بازبینی S2؛ مشاور S5؛ Agent 0 پذیرنده.**
**شاخه و SHA بازبینی:** PR #19 \`ec116f57c6f3950ff471549e24ce7bed7377af54\`، base \`mvp-python@618137014f6f41d82377b71e1ff9b5abd4337a51\`.
**هدف:** بازبینی مستقل S2 از \`RealMarkets.collect()/context()/inspect()\`، QuoteProducer و S2-compatible \`MarketBatch\`؛ اعتبار نرخ Gas، هویت پیکربندی، عبور امن از خطاهای عمق/تازگی/کارمزد، دادهٔ REAL و 250 HBAR؛ تشخیص عدم ارسال سفارش، زمان‌های RPC و WS/REST.
**مسیرهای بازرسی:** \`src/arb_multidex/connections/**\`، \`dex/**\`، \`kucoin/**\`، \`quotes/**\`، \`config/markets.example.yaml\`، \`tests/integration/test_real_market_readonly.py\`.
**تغییر کد توسط بازبین:** ممنوع (READ-ONLY).
**پذیرش:** تست کامل، Ruff، \`git diff --check\`، تست تطبیق S2 در هر دو جهت و سلب ادعای admissible تا زمانی که fees/gas/freshness معتبر نیستند؛ واقعی بودن منبع فقط با log/observation. آزمون عمومی فقط‌خواندنی OPT-IN؛ نبود اینترنت شکست کدنویسی محسوب نمی‌شود، اما عدم مشاهدهٔ واقعی را صریح ثبت کن.
**خروجی:** گزارش \`docs/reviews/MVP-02-S1-PR19-S2-review.md\` یا کامنت GitHub PR19 با PASS/REQUEST_CHANGES، exact SHA، شواهد و blockers. چون بازبینی READ-ONLY است، گزارش را در PR comment بدون دستکاری شاخه پیاده‌سازی ثبت کن. اگر نقص یافت شد S1 با همان شناسه \`MVP-02-S1-LIVE-MARKET / Revision 2+\` روی **همان PR19** اصلاح کند. **Agent 0 پس از پذیرش مستقل، PR19 را یک‌بار ادغام کند و SHA جدید \`mvp-python\` را ثبت کند.**

### STAGE B — MVP-02-CONFIG / Revision 1 (Issue #15)
**پیاده‌ساز S4؛ بازبین S5؛ مصرف‌کننده S3؛ Agent 0 ادغام‌کننده.**
**شروع:** پس از پذیرش/ادغام PR19، مبنای جدید \`mvp-python@<actual-merged-SHA>\`. شاخه \`mvp-02-secure-runtime-config\` را بدون force و با حفظ تاریخچه به مبنای ادغام‌شده منتقل کن.
**هدف:** Runtime واقعی S1→S2→S3 و کنترل \`simulation_enabled\` فقط برای **execution submission**؛ مسیر تصمیم و قیمت هر دو حالت REAL است. MOCK فقط با آرگومان explicit \`--offline-fixture\` مجاز و با برچسب MOCK.
**قرارداد دقیق:**
- \`simulation_enabled: true\` → REAL S1 quotes + real source validation + real read-only balances (در صورت دسترسی و مجوز معتبر)، S2 admission؛ DEX و KuCoin با **SimulatedExecutionAdapter**، بدون امضا/ارسال، موجودی جداگانه شبیه‌سازی، گزارش estimated profit و zero real orders.
- \`simulation_enabled: false\` → همان REAL quotes/S2؛ **LiveExecutionAdapter**‌های S3 برای هر دو leg. تا زمانی که STAGE C و تأیید S5 موجود نیست، false با \`LIVE_ADAPTER_NOT_READY\` رد شود؛ پس از آن، false قابلیت ورود به مسیر LIVE را دارد ولی گیت روشن‌کردن معاملات واقعی با تأیید صریح مالک جداگانه حفظ می‌شود.
- کلید strictly boolean، default true؛ هیچ رشتهٔ \`"False"\` یا truthiness پذیرفته نشود؛ swap حالت هنگام active/pending/unknown/recovery ممنوع؛ شناسه و فایل journal برای mode جدا؛ هر Worker mode مشخص؛ حداکثر سه Worker؛ بدون fallback از REAL به MOCK در اجرای معمول.
- \`.env.example\` با placeholder خالی، \`.gitignore\` برای \`.env\` و variants و \`*.sqlite3\`، cache/log/data/venv؛ \`.env\` در کامپیوتر/سرور مالک، هرگز Git. مسیرهای KuCoin \`KUCOIN_API_KEY/SECRET/PASSPHRASE\`، Hedera \`HEDERA_RPC_URL\`, \`HEDERA_ACCOUNT_ID\`, \`HEDERA_WALLET_ADDRESS\`, \`HEDERA_WALLET_PRIVATE_KEY\` یا signer-service reference فقط از محیط؛ در production signer/secret manager ترجیح دارد. Seed phrase وارد نکن. Credentials در SIMULATED SEND خوانده/لاگ/ارسال نشوند؛ برای read-only RPC کلید خصوصی لازم نیست؛ برای KuCoin account read-only ممکن است API read-only لازم شود.
- \`config/runtime.example.yaml\`: واقعی بودن داده، شبیه‌سازی فقط در execution، مقدار 250، سود 150000، mapping صحیح، fees/gas یا placeholders متمایز که fail-closed هستند؛ آزمایش با تنظیمات معتبر mock-transport بدون اتصال واقعی.
- CLI \`--help\`، \`status\`، \`simulate\` یا \`run\`، \`--read-only\` و \`--offline-fixture\` با معنای صریح. شناسه market source، timestamps و reasons/fee/real_vs_mock در خروجی. افشای secrets صفر.
**مسیرهای مجاز:** \`src/arb_multidex/config/**\`, \`app.py\`, \`cli.py\`, \`lifecycle/**\`, \`observability/**\`, \`__main__.py\`, \`.env.example\`, \`.gitignore\`, \`config/runtime.example.yaml\`, \`README.md\`, \`pyproject.toml\`, tests S4/config/integration. **S1/S2/S3 بدون درخواست و هماهنگی Agent 0 تغییر نکنند.**
**آزمون:** real market mock transports for deterministic CI; true zero external order calls; false invokes correct injectable boundary or fail-closed; invalid bool; no fallback; modes isolated; dirty/unknown journal blocks transition; S5 verifies credential redaction, restart and fail-closed. Full pytest, Ruff, diff check, executable CLI.
**تحویل:** PR به \`mvp-python\`، SHA، changed files، sample config و CLI transcript و tests و S5 result؛ بدون Merge توسط S4.

### STAGE C — MVP-02-S3-LIVE-EXEC / Revision 1 (Issue #17)
**پیاده‌ساز S3؛ بازبین S5؛ مصرف‌کننده S4؛ مشاور S1/S2؛ Agent 0 ادغام‌کننده.**
**شروع:** پس از STAGE B و تثبیت API mode و SHA جدید \`mvp-python\`؛ شاخه تازه \`mvp-02-s3-live-execution\` از SHA جدید.
**هدف:** پیاده‌سازی واقعی (نه stub بی‌اثر) دو adapter قابل تزریق:
- \`RealDexExecutionAdapter\`: اتصال به Hedera chain 295، کنترل chain/pool/token/router/spender از allowlist و تأیید هویت، wallet signing محلی/managed، کنترل key/address/nonce/allowance/approval، slippage/deadline/Gas upper bound، nonce reservation، hash receipt lookup، unknown/timeout/crash recovery و جلوگیری از امضای تکراری. امضای ناشناس و infinite approval پیش‌فرض ممنوع. توکن‌ها و gas balance قبل از شروع کنترل شود.
- \`RealKuCoinExecutionAdapter\`: API authentication least privilege (spot trade, بدون withdraw)، clientOrderId durable + idempotency، ثبت request و retry حدود ۳ بار با reconciliation قبل از retry؛ انواع جزئی fill/fee/unknown، audit و wallet/exchange balance reconciliation؛ exchange precision/minNotional.
- DEX first و KuCoin فقط با تأیید DEX؛ حداکثر سه تلاش برای هر leg؛ five \`INCOMPLETE_ARBITRAGE\` consecutive -> \`TRADING_PAUSED\` قابل راه‌اندازی فقط دستی؛ unknown holds remain fenced، recovery first؛ two-phase durable intent before external side-effect؛ replay بدون سفارش تکراری. حداکثر ۳ Worker و atomic shared KuCoin inventory.
- **Adapter mode:** S1/S2 quote/admission مشترک در true/false؛ در true فقط ارسال‌ها شبیه‌سازی شوند؛ در false هر دو leg LIVE و فقط با گیت فعال‌سازی مستقل (سپس رضایت مالک). هیچ MIXED mode در یک arbitrage.
- Authenticated account balances can be read from KuCoin / Hedera by signer address in READ-ONLY path; accounting sim and live isolated, no assumptions from mock startup amounts.
- Immutable config and explicit wallet/account ownership per worker, spend cap, kill switch, safe stop/restart, gas slippage and allowance caps; expose required owner decisions as config validators rather than invent fixed live limits.
**مسیرهای مجاز:** \`src/arb_multidex/execution/**\`, focused \`tests/unit/execution/**\`, \`tests/integration/test_live_adapters_offline.py\` و S3 owned docs. Runtime API adjustments outside execution require separate S4-reviewed patch/PR.
**آزمون:** offline injected fake RPC and KuCoin transports; no funded wallet, no real send during CI. Verify chain mismatch, nonce uniqueness, missing approvals, wrong spender, stale quote, unauthenticated exchange, partial fills, receipts unknown, repeated retries, crash after DEX before KuCoin, concurrent workers, restart, secrets absent from logs. Optional real endpoint **read-only** tests only; NO automatic test of funded trades.
**خروجی:** PR، API examples، exact request/response and state models, proof of offline transport assertions, list of decisions needing owner signoff, exact SHA/test/CI/S5 review. Agent 0 may integrate reviewed code; must NOT execute real money orders as part of verification.

### STAGE D — MVP-02-TESTS / Revision 1 (Issue #16)
**پیاده‌ساز S5؛ بازبین Agent 0؛ مشورت S1–S4.**
**شروع:** پس از ادغام C، branch \`mvp-02-per-module-test-runners\` از SHA جدید \`mvp-python\`.
**هدف:** فایل مستقل قابل اجرای کاربر برای هر ماژول:
\`scripts/test_connections.py\`, \`test_dex.py\`, \`test_kucoin.py\`, \`test_quotes.py\`, \`test_opportunity.py\`, \`test_execution.py\`, \`test_lifecycle.py\`, \`test_observability.py\`, \`test_integration.py\`, \`test_all.py\`.
- هر فایل با \`python scripts/test_dex.py\` و همان الگو مستقل اجرا شود؛ pytest path درست \`tests/unit/{connections,dex,market_data,opportunity,execution,lifecycle,observability}\` و tests/integration؛ subprocess + \`sys.executable\` با exit code حقیقی، سازگاری Windows/Linux/macOS در حد runner و بدون \`fcntl\` در خود test launcher.
- \`test_connections.py\` و \`test_dex.py\` و \`test_kucoin.py\` option \`--real-readonly\` برای مشاهده actual RPC/orderbook و wallet/exchange balance در صورت مجوز؛ **بدون امضا/تراکنش/سفارش**؛ نبود اینترنت = result unavailable/skip واضح، نه quote MOCK به‌جای LIVE.
- runner اجرای تست LIVE trading نداشته باشد؛ امنیت secret redaction و عدم نوشته‌شدن \`.env\`/journal در repo بررسی شود؛ CI هر runner را به‌صورت مستقل اجرا کند؛ docs فارسی ساده و command examples.
**مسیرها:** \`scripts/**\`, \`tests/**\`, \`README.md\`, \`.github/workflows/**\` فقط برای test orchestration؛ کد عملیاتی S1–S4 را بدون issue اصلاح نکند.
**آزمون:** اجرای واقعی تمام ۱۰ runner (offline)، integration happy/unhappy, full pytest/Ruff/diff; گزارش شمارش دقیق و exit code. PR مستقل، بازبینی Agent 0.

### STAGE E — MVP-02-FINAL-S5 / Revision 1 + REPO-FINALIZATION
**مالک بازبینی S5؛ ناظر مستقل Agent 0؛ مشاور هر تخصص.**
**شروع:** پس از ادغام A–D؛ SHA دقیق \`mvp-python\` و commit-hashهای هر بسته در GitHub ثبت شوند.
**هدف:** security & full flow review روی **یک درخت واقعی** با S1 REAL market data + S2 durable admission + S3 live adapter + S4 mode switch + all test scripts.
- Run offline fully deterministic end-to-end with both values of simulation_enabled and fake external transports; assert true real-source quotes but zero external sends, false correct adapter dispatch with opt-in blocked absent owner authorization; app must not label synthetic/fixture quotes REAL. Runtime with internet opt-in REAL-readonly must report true source, timestamps, fees/gas/source status and may correctly report NO_OP for stale/missing cost data. Do not manufacture profitable arbitrage.
- Audit S2/S3 atomics and initial balance declarations; serializability, nonce/clientOrderId, partial fills, retry limits, unknown receipts, five incomplete pause, restarting workers, safe stop, cross-mode transitions, .env credential leak/permissions, slippage, gas and Approval, contract allowlists; no actual funded sends.
- Confirm user can follow README on a clean environment and run every independent runner; README must separate Windows caveat for POSIX flock and contain honest Linux/macOS runtime setup.
- Zero unresolved BLOCKER/MAJOR, passing CI on final SHA and PR lineage, release notes, configuration reference, security checklist, runbook with start/stop/backup/recovery, examples \`.env.example\` and real-market config, no secrets; record owner-open live decisions and final readiness status.
- No automatic LIVE activation. Actual transaction pilot must be separate explicitly approved owner action after reviewing real wallet custody, fee/risk limits, and authenticated connectivity. If this has not occurred, deliver **CODE-READY / LIVE-NOT-ACTIVATED**, not a false claim of production runtime proof.
**خروجی:** \`docs/reviews/MVP-02-final-acceptance.md\` (Agent 0 checks), versioned tag/release proposal after acceptance, final SHA, list of merged PRs, full CI URLs, truthful remaining issues, exact runnable commands and stateful backup/recovery requirements. Only Agent 0 merges accepted PRs.

## 3) ترتیب شاخه‌ها و سیاست خطا
1. Stage A review PR19, S1 corrections if needed, S2 independent PASS, Agent 0 merge.
2. Stage B S4 config/runtime, S5 review, Agent 0 merge.
3. Stage C S3 live code adapters + security tests, S5 review, Agent 0 merge.
4. Stage D S5 test runners, Agent 0 review+merge.
5. Stage E final S5 review, Agent 0 release decision.
- در مراحل قابل اجرا بدون تصمیم جدید مالک، پیاده‌سازی را ادامه بده؛ تصمیم‌های اساسی یا فقدان شواهد فنی را **مخفی/حدس نزن**. اگر blocked شد، در Issue و report با کد و پیام دقیق ثبت کن و روی بخش‌های مستقل ادامه بده.
- روی یک شاخهٔ اختصاصی و PR همان Task کار کن؛ بدون Force-Push یا Merge به شاخه Integration. \`agent-governance\` محل قراردادهاست، \`mvp-python\` شاخه ادغام کد. PR19 در وضعیت OPEN است و بازبینی آن first gate است.
- هر بار خروجی Codex باید در GitHub بازیابی شود؛ یک «نقش S1/S2/S3» در سند به معنای وجود ایجنت اتوماتیک نیست. Codex می‌تواند نقش‌های متخصص را با جداسازی commit و reviewer pass در جلسات مستقل اجرا کند. اجرای همه مراحل یک‌جا اگر از budget context خارج شد ممنوع؛ **پس از هر Stage کد/گزارش را commit و PR کن و مرحله بعدی را از همین فایل بخوان**.
- در نبود API/plugin برای تعامل با reviewer مستقل، \`READY_FOR_AGENT0_REVIEW\` یا \`REVIEW_REQUIRED\` بده؛ هیچ تأییدی را خودت جعل نکن و هرگز ادغام را انجام‌شده نخوان مگر GitHub واقعاً merged نشان دهد.
- آزمون LIVE با ارز واقعی و کلید خصوصی را اجرا نکن، حتی اگر همه تست‌های آفلاین PASS باشند؛ مالک باید بعداً صراحتاً start/volume/limits را تأیید کند.

## 4) قالب ثابت گزارش پایانی هر Stage

\`\`\`text
TASK-ID / REVISION:
ROLE implementer / independent reviewer:
Governance SHA / base branch SHA / head SHA:
Issue / PR / CI URLs:
Changed files with reasons:
Summary of implemented behavior:
Tests: exact commands / passed / skipped / failed / warnings:
Independent review: PASS / REQUEST_CHANGES / PENDING, link:
Security: no secret committed / no real orders sent / mode isolation:
Real public observation: source / timestamp / depth / fees / freshness / errors, if actually queried:
Open owner decisions and blocking evidence:
Status: READY_FOR_AGENT0_REVIEW | REQUEST_CHANGES | BLOCKED
Next stage and concrete gate:
\`\`\`

## 5) دستور کوتاه قابل استفاده مجدد توسط مالک

**Codex:** مخزن \`elvito2808/ARB-MultiDex\` را باز کن. در شاخه \`agent-governance\` پرونده \`docs/agents/FINALIZATION-RUNBOOK.md\` را کامل بخوان؛ اسناد حاکم و refهای روز را تأیید کن. از **اولین STAGE ناتمام** مطابق گیت‌ها آغاز کن. فقط همان stage را اجرا کن؛ commit/PR/CI و گزارش دقیق را در GitHub ثبت کن؛ اگر مرحله بازبینی مستقل لازم است، به Agent 0 تحویل بده و بدون Merge/تأیید ساختگی وارد مرحله وابسته نشو. همه مراحل دیگر در همین فایل هستند، و در پاسخ لازم نیست متن بلند دوباره کپی شود.
