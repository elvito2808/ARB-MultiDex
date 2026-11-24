# قرارداد Quote در شبیه‌سازی

## SIM-01 / بازنگری ۳ (Revision 3)

| فیلد تحویل | مقدار |
| --- | --- |
| فرستنده / گیرنده / بازبین | Agent 0 / S1 / S2، با مشورت S5 |
| حاکمیت | `agent-governance@9acb21a38c4cacbef8af061ab86a09f06913dec2` |
| مبنای پیاده‌سازی | `4c8f77f9ade041bc3a8051758817c9a6aea79adf` |
| مرجع بازبینی | [S2 Review-2، یافتهٔ B1](https://github.com/elvito2808/ARB-MultiDex/blob/523715e06f6a01933895aae65e896d32e4cfa48e/docs/reviews/SIM-01-S2-review-2.md) در `523715e06f6a01933895aae65e896d32e4cfa48e` |
| مسیر مجاز | `docs/design/quote-contract.md` |
| وابستگی و تصمیم باز | نگاشت دارایی و طرفین در D-01، فناوری در D-02، سیاست حجم، زمان و هزینه در D-03 |
| وضعیت | `READY_FOR_REVIEW` برای بازبینی دستی S2 |
| اقدام بعدی | S2 معیارها را با شواهد روی SHA انتشار بازبینی کند. Agent 0 پس از ثبت نظر بازبین، مبنا را با مقصد مقایسه و پذیرش را ثبت کند. |

مراجع طراحی شامل [DEX، بخش‌های ۶ تا ۹](../../DEX_Module_Requirements_v1.0.md)، [KuCoin، بخش‌های ۳ تا ۱۰](../../KuCoin_Module_Requirements_v1.0.md)، [Connections، بخش‌های ۵ و ۹](../../Connections_Module_Requirements_v1.0.md)، [تشخیص فرصت، بخش‌های ۴ تا ۶ و ۹](../../Arbitrage_Opportunity_Module_Requirements_v1.0.md) و [امنیت و آزمون، بخش‌های ۸ و ۱۵ تا ۱۷](../../Project_Security_and_Testing_Requirements_v1.0.md) هستند. نقش‌ها طبق [تحویل دستی](../agents/handoff.md) تعریف شده‌اند. مرجع نتیجه، کامیت منتشرکنندهٔ این فایل است.

## درخواست، پاسخ و هویت بازار

درخواست دقیقاً شامل `requestId`، `relationshipId`، `mappingRevision`، `feePolicyId`، `mode`، `sourceId`، `side`، `baseAssetId`، `settlementAssetId`، `baseUnits`، `baseScale` و `settlementScale` است. موتور برای هر سمت و هر تلاش شناسهٔ یکتای تازه می‌دهد و درخواست اصلی را تا پایان بررسی نگه می‌دارد. envelope موفق `status=OK` همهٔ فیلدهای درخواست را عیناً بازمی‌گرداند و `settlementUnits`، `grossSettlementUnits`، `includedCosts`، `observedAt` و `connectionEpoch` را اضافه می‌کند. envelope خطای Quote دقیقاً چهار فیلد `status=ERROR`، `requestId`، `sourceId` و `code` دارد. مسیر فراخوانی باید حتی برای پاسخ ناخوانا یا درخواست فاقد شناسه، هم‌بستگی مستقل با فراخوانی را حفظ کند. در خطای ورودی، فیلد هم‌بستگیِ نامعتبر یا غایب `null` است. هیچ پاسخی به فراخوانی دیگری منتقل نمی‌شود.

`sourceId` ارجاع تغییرناپذیر به دقیقاً یک بازار ثبت‌شده است. رکورد بازار شامل شناسهٔ venue، شبکه (در صورت کاربرد)، جفت مرتب‌شدهٔ پایه/تسویه با شناسهٔ بومی هر دارایی و شناسهٔ pool انتخاب‌شده (در صورت کاربرد) است. تغییر هر بعد هویت مستلزم شناسهٔ تازه است. شناسه‌های دارایی در فضای نام منبع‌اند. بازنگری تغییرناپذیر `mappingRevision` در محدودهٔ `relationshipId`، بازارهای مجاز هر جهت، مقیاس‌های محلی، هم‌ارزی پایه و نگاشت صریح ۱:۱ تسویه را مشخص می‌کند. موتور هر دو سمت را به جهت، بازار، حالت، بازنگری فعال و سیاست همان رابطه مقید می‌کند. شباهت نماد هیچ هم‌ارزی‌ای ایجاد نمی‌کند. انتخاب بازارها و ارائه‌دهندگان در D-01 باز است.

### قالب و حساب دقیق

هر پیام یک شیء JSON حداکثر ۱۶٬۳۸۴ بایت UTF-8 با کلیدهای یکتا و فیلدهای تعریف‌شده است. شناسه‌ها توکن حساس به حروف با ۱ تا ۱۲۸ نویسه از مجموعهٔ ASCII `[A-Za-z0-9._:/-]` هستند. `side` یکی از `BUY` و `SELL` است. `mode` یکی از `SIMULATION` و `LIVE` است و این مرحله فقط درخواست شبیه‌سازی را پشتیبانی می‌کند. مقادیر مثبت واحد خرد، شامل حجم پایه و مبلغ نهایی تسویه، رشته‌های ASCII با الگوی تطبیق کامل `[1-9][0-9]{0,77}` هستند. برای مقدار صراحتاً نامنفی، تنها شکل صفر `"0"` است. علامت، فاصله، صفر ابتدایی، اعشار، نما و رقم غیر ASCII ممنوع‌اند. scale عدد صحیح JSON با نمایش ده‌دهی بدون علامت، اعشار یا نما در بازهٔ ۰ تا ۳۸ و برابر مقیاس ثبت‌شدهٔ دارایی است.

`units / 10^scale` مقدار دارایی را بیان می‌کند. حجم پایه هر دو سمت مقدار دقیق منتقل‌شده برای کاربر است. مقدار بومی غیرقابل‌نمایش در مقیاس قرارداد یا مبلغ نهایی صفر/منفی، از جمله فروش با دریافتی خالص صفر، `INVALID_QUOTE` است. استثنای گردکردن فقط تبدیل هزینه طبق بخش بعد است. موتور اختلاف مقیاس را با ضرب صحیح در توان ۱۰ رفع می‌کند و با اعداد صحیح با دقت دلخواه محاسبه می‌کند. نرخ واحد فقط برای نمایش است.

## پوشش و تطبیق هزینه

`feePolicyId` ارجاع تغییرناپذیر مشترک هر دو سمت به سیاست رابطه است. سیاست، شناسهٔ مؤلفه‌های الزامی برای هر بازار و سمت و منبع و اعتبار نرخ تبدیل را تعیین می‌کند. `includedCosts` آرایهٔ حداکثر ۳۲ رکورد و شامل دقیقاً یک رکورد برای هر مؤلفهٔ الزامی، حتی هزینهٔ معلومِ صفر، است. هر رکورد شامل `costId` و مقدار نامنفی `settlementCostUnits` و `treatment` با مقدار `EMBEDDED` یا `SEPARATE` است. هزینهٔ مستتر در قیمت یا مسیر معامله با `EMBEDDED` مشخص می‌شود و مقدار آن در حساب واسط لحاظ شده است. `grossSettlementUnits` مبلغ مثبت معامله پس از هزینه‌های مستتر و پیش از هزینه‌های جداگانه است. هزینهٔ جداگانه در BUY به این مبلغ اضافه و در SELL از آن کم می‌شود. مبلغ نهایی BUY کل پرداختی و مبلغ نهایی SELL دریافتی خالص در واحد تسویه است. موتور اختلاف دو مبلغ نهایی را می‌گیرد و هیچ رکورد هزینه‌ای را دوباره کسر نمی‌کند.

برای `SEPARATE`، رکورد همچنین `assetId`، `units` نامنفی، `scale` و نرخ مثبت کسری `rateNumerator` و `rateDenominator` را دارد. نرخ، تعداد واحد خرد تسویه به ازای یک واحد خرد دارایی هزینه است. در واحد تسویه نرخ دقیق ۱:۱ است. برای دارایی دیگر، رکورد `rateRef` نیز دارد که به مشاهدهٔ تغییرناپذیر نرخ و زمان آن ارجاع می‌دهد. منبع نرخ را طبق سیاست و در زمان اعتبارسنجی Quote بررسی می‌کند. مبلغ هر رکورد برابر سقف صحیح `units * rateNumerator / rateDenominator` است و گردکردن در کوچک‌ترین واحد تسویه و یک‌بار برای هر مؤلفه انجام می‌شود. مبلغ گزارش‌شدهٔ هزینهٔ مستتر، تغییر مبلغ تسویه ناشی از همان مؤلفه است. تولیدکننده باید مبنای قابل‌انتساب آن را از مدل قیمت‌گذاری همان معامله به دست آورد و برای هر تبدیل بومی همین قاعدهٔ نرخ و گردکردن را رعایت کند. رکوردهای مستتر فقط سه فیلد مشترک را دارند.

هزینه‌ای که از جریان پایه برداشته می‌شود در مسیر قیمت‌گذاری مستتر است. BUY باید مقدار معامله را طوری تعیین کند که پایهٔ دریافتی پس از کسر هزینه دقیقاً حجم درخواست باشد. SELL باید پایهٔ ورودی معامله را طوری تعیین کند که ورودی به‌علاوهٔ هزینهٔ پایه دقیقاً حجم کسرشده از کاربر باشد. مبلغ معامله با قیمت‌گذاری مجدد همان مقدار واقعی محاسبه می‌شود. اضافه یا کم کردن دوبارهٔ معادل تسویهٔ این هزینه مجاز نیست. هزینهٔ پایه‌ای که از پرداخت جداگانه تأمین می‌شود تنها در صورتی قابل قبول است که کل تغییر موجودی پایه همچنان دقیقاً حجم درخواستی باشد و مدل قیمت‌گذاری آن را مستتر ثبت کند. اگر حجم دقیق ناممکن باشد، `INVALID_QUOTE` است. هر هزینهٔ الزامی غایب، نامعلوم یا دارای تبدیل نامعتبر، کهنه یا غیرقابل‌نمایش `FEE_UNAVAILABLE` است. نبود نقدشوندگی برای اجرای حجم دقیق `SOURCE_UNAVAILABLE` است. انتخاب مؤلفه‌ها، سیاست و نرخ‌های واقعی تحت D-03 باقی می‌ماند.

## زمان، کش و بازیابی

`observedAt` زمان مشاهدهٔ وضعیت بازار است و دقیقاً قالب UTC `YYYY-MM-DDTHH:mm:ss.SSSZ` با تقویم میلادی معتبر، سال ۰۰۰۱ تا ۹۹۹۹ و ثانیهٔ ۰۰ تا ۵۹ دارد. offset، ثانیهٔ کبیسه و دقت دیگر پذیرفته نمی‌شوند. زمان دقیق‌تر به ابتدای میلی‌ثانیهٔ مشاهده تبدیل می‌شود. تولیدکننده پیش از انتشار و موتور هنگام پذیرش یا استفادهٔ مجدد از ساعت مرجع UTC همگام (در شبیه‌سازی ساعت تزریق‌شدهٔ مشترک) استفاده می‌کنند. موتور برای هر جفت فقط یک لحظهٔ ارزیابی می‌خواند. دقیقه یا زمان ورود به کش جانشین زمان مشاهده نمی‌شود. سن منفی `INVALID_QUOTE` و سن بزرگ‌تر یا مساوی سقف سن منبع `STALE_QUOTE` است. اختلاف مطلق دو مشاهده فقط اگر از سقف اختلاف رابطه بزرگ‌تر باشد `QUOTE_TIME_SKEW` است. ساعت یا وضعیت اتصال نامطمئن موجب توقف انتشار موفق برای منبع می‌شود. سقف‌ها به میلی‌ثانیهٔ صحیح، سن مثبت و اختلاف نامنفی‌اند و مقدار واقعی زیر D-03 تعیین می‌شود.

`connectionEpoch` شناسهٔ نسل اتصال در محدودهٔ حالت و بازار است و با هر بازیابی عوض می‌شود. تولیدکننده و موتور در قطع اتصال یا از دست رفتن پیوستگی، همهٔ رکوردهای کش و پاسخ‌های در پرواز نسل قبلی را برای بازار/حالت متأثر نامعتبر می‌کنند. خواندن کش و انتشار باید با وضعیت نسل جاری سازگار باشد. کلید کش شامل حالت، رابطه، بازنگری، بازار، جهت، شناسه‌ها و مقیاس‌های دارایی، حجم، سیاست و نسل است. استفادهٔ مجدد فقط برای ورودی‌های یکسان مجاز است و شناسهٔ درخواست اصلی، زمان مشاهده و دادهٔ قیمت ثابت می‌مانند. این استفاده به معنای ارسال مجدد پاسخ برای درخواست تازه نیست. هر درخواست تازه باید envelope هم‌بستهٔ خودش را از تولیدکننده بگیرد، حتی اگر تولیدکننده از مشاهدهٔ معتبر مشترک استفاده کند.

برای هر بازار و حالت، تولیدکننده آخرین زمان مشاهدهٔ پذیرفته‌شده را از قطع اتصال عبور می‌دهد. در یک نسل، مشاهدهٔ قدیمی‌تر مردود است. یک snapshot معتبر می‌تواند به چند درخواست پاسخ دهد. مشاهده‌ای متفاوت با برچسب زمانی یکسان یا تکرار رویداد ورودی نمی‌تواند مشاهدهٔ جدید محسوب شود یا اعتبار قبلی را تمدید کند. پس از بازیابی، فقط snapshot اخذشده در نسل جاری با زمان مشاهده دست‌کم برابر زمان بازیابی و اکیداً جدیدتر از آخرین مشاهدهٔ پذیرفته‌شده می‌تواند صلاحیت را برگرداند. snapshot بازیابی باید همهٔ بررسی‌های ساختاری، هزینه و تازگی را بگذراند. دادهٔ replay یا خارج از ترتیب `INVALID_QUOTE` است و کش را جایگزین نمی‌کند. موتور با نسل جاری و وضعیت بازیابی، پاسخ نسل قبلی را رد می‌کند و سن و اختلاف مشاهده را در هر تصمیم دوباره می‌سنجد.

## تقدم، محدودهٔ خطا و مصرف‌کننده

تولیدکننده در هر درخواست، به ترتیب زیر نخستین خطای قابل‌تشخیص را گزارش می‌کند. مراحل وابسته به snapshot فقط با وجود دادهٔ لازم اجرا می‌شوند. موتور envelope و همهٔ فیلدهای echo را در برابر درخواست اصلی می‌سنجد، خطای هم‌بستهٔ معتبر را به همان درخواست نسبت می‌دهد و هر success را با همین ترتیب تا حد داده و وضعیت در اختیار خود دوباره بررسی می‌کند. تولیدکننده مسئول اثبات عمق، مشاهده و مدل هزینه است. موتور مسئول انطباق با تنظیمات فعال، محاسبهٔ تطبیق هزینه و مقایسهٔ دو سمت است.

| تقدم درخواست | `code` | شرط |
| --- | --- | --- |
| ۱ | `INVALID_QUOTE` | ورودی یا envelope نامعتبر، کد ناشناخته، هم‌بستگی نادرست، مغایرت هر فیلد echo (از جمله بازار، حالت، دارایی، بازنگری یا حجم)، حالت پشتیبانی‌نشده، زمان آینده، replay، نسل نامعتبر، یا مقدار غیرقابل‌نمایش یا نامثبت. اگر همهٔ مؤلفه‌های لازم معتبر باشند، ناسازگاری مبلغ نهایی با جمع آن‌ها نیز همین کد را دارد. در صورت نبود مؤلفه یا تبدیل معتبر، آزمون تطبیق مبلغ قابل‌اجرا نیست. |
| ۲ | `SOURCE_UNAVAILABLE` | قطع، بازیابی تأییدنشده، نبود ساعت قابل اتکا یا داده/عمق کافی. |
| ۳ | `STALE_QUOTE` | سن مشاهده به سقف رسیده یا از آن گذشته است. |
| ۴ | `FEE_UNAVAILABLE` | پوشش یا تبدیل هزینهٔ الزامی قابل تأیید نیست. |

موتور پیش از مقایسه، هر دو درخواست را مستقل بررسی می‌کند. اگر هر دو خطا بدهند هر دو انتساب حفظ می‌شود و کد اصلی با تقدم جدول و در تساوی با تقدم BUY تعیین می‌شود. هر خطای درخواست بر خطای جفت مقدم است. پس از اعتبارسنجی مستقل، تقدم خطای جفت به‌ترتیب زیر است.

| تقدم جفت | `code` | شرط |
| --- | --- | --- |
| ۱ | `INCOMPARABLE_QUOTES` | حالت‌ها متفاوت‌اند یا با حالت تصمیم سازگار نیستند. همچنین انتساب معتبرِ مستقل به بازار، جهت، رابطه، بازنگری فعال یا سیاست فعال جفت نمی‌خواند. |
| ۲ | `ASSET_MISMATCH` | نگاشت فعال هم‌ارزی پایه یا نسبت ۱:۱ تسویه را تأیید نمی‌کند. |
| ۳ | `INCOMPARABLE_QUOTES` | حجم دو درخواست مستقل در واحد پایهٔ مشترک برابر نیست، یا حجم مشترک با حجم تنظیم‌شدهٔ جهت برابر نیست. |
| ۴ | `QUOTE_TIME_SKEW` | اختلاف زمان دو مشاهده از سقف رابطه گذشته است. |

نتیجهٔ خطای جفت شامل `relationshipId`، جهت، شناسهٔ هر دو درخواست و `code` است و فقط همان جهت را متوقف می‌کند. خطای درخواست فقط مصرف‌کنندگان همان ورودی را متوقف می‌کند. رابطه‌ها و جهت‌های دیگر با ورودی‌های معتبر مستقل ادامه می‌دهند. کش و مسیر تصمیم بر حسب حالت ایزوله‌اند. موتور پیش از قیمت‌سنجی یا استفاده از کش، حالت هر دو سمت را با حالت تصمیم تطبیق می‌دهد. هیچ دادهٔ شبیه‌سازی برای تصمیم LIVE واجد شرایط نیست.

موتور برای جهت A→B از BUY بازار A و SELL بازار B و برای جهت B→A از BUY بازار B و SELL بازار A استفاده می‌کند. هر جهت به حجم پیکربندی‌شدهٔ خودش مقید است و جداگانه `SELL.settlementUnits - BUY.settlementUnits` را پس از تطبیق دقیق مقیاس محاسبه می‌کند. طبق تصمیم صریح مالک در 2026-11-24، اختلاف خالص **برابر یا بزرگ‌تر از آستانهٔ همان جهت**، پس از همهٔ کنترل‌های صلاحیت، فرصت است. فقط اختلاف کمتر از آستانه، نتیجهٔ معتبر بدون فرصت است. این سیاست جایگزین قاعدهٔ قدیمی «اکیداً بزرگ‌تر» می‌شود.

## نمونه‌های ساختگی و شواهد آفلاین

بازارهای `sim-a` و `sim-b` هر کدام venue، شبکه، جفت و pool ساختگی و متمایز دارند. در رابطهٔ `sim-ab` و بازنگری `m1`، دارایی‌های محلی `asset-x` هم‌ارز و `asset-y` با نسبت ۱:۱ هستند. حجم هر دو جهت ۲۰۰ واحد خرد در مقیاس ۲، سقف سن ۵۰۰۰ میلی‌ثانیه، سقف اختلاف ۲۰۰۰ میلی‌ثانیه و زمان ارزیابی `2026-11-23T00:00:02.000Z` است. سیاست ساختگی `sim-fees-1` در جهت رفت هیچ مؤلفه‌ای و در جهت برگشت برای BUY هزینهٔ جداگانهٔ `gas` و برای SELL هزینهٔ مستتر `base-fee` می‌خواهد. نرخ ساختگی `sim-rate-1` معتبر در زمان ارزیابی است. نام‌های بازار و مقادیر سیاست صرفاً دادهٔ آزمون‌اند.

```json
[
  {
    "request": {
      "requestId": "q1",
      "relationshipId": "sim-ab",
      "mappingRevision": "m1",
      "feePolicyId": "sim-fees-1",
      "mode": "SIMULATION",
      "sourceId": "sim-a",
      "side": "BUY",
      "baseAssetId": "sim-a/asset-x",
      "settlementAssetId": "sim-a/asset-y",
      "baseUnits": "200",
      "baseScale": 2,
      "settlementScale": 2
    },
    "response": {
      "status": "OK",
      "requestId": "q1",
      "relationshipId": "sim-ab",
      "mappingRevision": "m1",
      "feePolicyId": "sim-fees-1",
      "mode": "SIMULATION",
      "sourceId": "sim-a",
      "side": "BUY",
      "baseAssetId": "sim-a/asset-x",
      "settlementAssetId": "sim-a/asset-y",
      "baseUnits": "200",
      "baseScale": 2,
      "settlementScale": 2,
      "grossSettlementUnits": "10125",
      "settlementUnits": "10125",
      "includedCosts": [],
      "observedAt": "2026-11-23T00:00:00.000Z",
      "connectionEpoch": "e1"
    }
  },
  {
    "request": {
      "requestId": "q2",
      "relationshipId": "sim-ab",
      "mappingRevision": "m1",
      "feePolicyId": "sim-fees-1",
      "mode": "SIMULATION",
      "sourceId": "sim-b",
      "side": "SELL",
      "baseAssetId": "sim-b/asset-x",
      "settlementAssetId": "sim-b/asset-y",
      "baseUnits": "200",
      "baseScale": 2,
      "settlementScale": 2
    },
    "response": {
      "status": "OK",
      "requestId": "q2",
      "relationshipId": "sim-ab",
      "mappingRevision": "m1",
      "feePolicyId": "sim-fees-1",
      "mode": "SIMULATION",
      "sourceId": "sim-b",
      "side": "SELL",
      "baseAssetId": "sim-b/asset-x",
      "settlementAssetId": "sim-b/asset-y",
      "baseUnits": "200",
      "baseScale": 2,
      "settlementScale": 2,
      "grossSettlementUnits": "10450",
      "settlementUnits": "10450",
      "includedCosts": [],
      "observedAt": "2026-11-23T00:00:01.000Z",
      "connectionEpoch": "e1"
    }
  },
  {
    "request": {
      "requestId": "q3",
      "relationshipId": "sim-ab",
      "mappingRevision": "m1",
      "feePolicyId": "sim-fees-1",
      "mode": "SIMULATION",
      "sourceId": "sim-b",
      "side": "BUY",
      "baseAssetId": "sim-b/asset-x",
      "settlementAssetId": "sim-b/asset-y",
      "baseUnits": "200",
      "baseScale": 2,
      "settlementScale": 2
    },
    "response": {
      "status": "OK",
      "requestId": "q3",
      "relationshipId": "sim-ab",
      "mappingRevision": "m1",
      "feePolicyId": "sim-fees-1",
      "mode": "SIMULATION",
      "sourceId": "sim-b",
      "side": "BUY",
      "baseAssetId": "sim-b/asset-x",
      "settlementAssetId": "sim-b/asset-y",
      "baseUnits": "200",
      "baseScale": 2,
      "settlementScale": 2,
      "grossSettlementUnits": "10000",
      "settlementUnits": "10003",
      "includedCosts": [
        {
          "costId": "gas",
          "settlementCostUnits": "3",
          "treatment": "SEPARATE",
          "assetId": "sim-b/gas",
          "units": "5",
          "scale": 0,
          "rateNumerator": "1",
          "rateDenominator": "2",
          "rateRef": "sim-rate-1"
        }
      ],
      "observedAt": "2026-11-23T00:00:01.000Z",
      "connectionEpoch": "e1"
    }
  },
  {
    "request": {
      "requestId": "q4",
      "relationshipId": "sim-ab",
      "mappingRevision": "m1",
      "feePolicyId": "sim-fees-1",
      "mode": "SIMULATION",
      "sourceId": "sim-a",
      "side": "SELL",
      "baseAssetId": "sim-a/asset-x",
      "settlementAssetId": "sim-a/asset-y",
      "baseUnits": "200",
      "baseScale": 2,
      "settlementScale": 2
    },
    "response": {
      "status": "OK",
      "requestId": "q4",
      "relationshipId": "sim-ab",
      "mappingRevision": "m1",
      "feePolicyId": "sim-fees-1",
      "mode": "SIMULATION",
      "sourceId": "sim-a",
      "side": "SELL",
      "baseAssetId": "sim-a/asset-x",
      "settlementAssetId": "sim-a/asset-y",
      "baseUnits": "200",
      "baseScale": 2,
      "settlementScale": 2,
      "grossSettlementUnits": "10245",
      "settlementUnits": "10245",
      "includedCosts": [
        {
          "costId": "base-fee",
          "settlementCostUnits": "55",
          "treatment": "EMBEDDED"
        }
      ],
      "observedAt": "2026-11-23T00:00:00.000Z",
      "connectionEpoch": "e1"
    }
  },
  {
    "request": {
      "requestId": "q5",
      "relationshipId": "sim-ab",
      "mappingRevision": "m1",
      "feePolicyId": "sim-fees-1",
      "mode": "SIMULATION",
      "sourceId": "sim-b",
      "side": "SELL",
      "baseAssetId": "sim-b/asset-x",
      "settlementAssetId": "sim-b/asset-y",
      "baseUnits": "201",
      "baseScale": 2,
      "settlementScale": 2
    },
    "response": {
      "status": "OK",
      "requestId": "q5",
      "relationshipId": "sim-ab",
      "mappingRevision": "m1",
      "feePolicyId": "sim-fees-1",
      "mode": "SIMULATION",
      "sourceId": "sim-b",
      "side": "SELL",
      "baseAssetId": "sim-b/asset-x",
      "settlementAssetId": "sim-b/asset-y",
      "baseUnits": "201",
      "baseScale": 2,
      "settlementScale": 2,
      "grossSettlementUnits": "10502",
      "settlementUnits": "10502",
      "includedCosts": [],
      "observedAt": "2026-11-23T00:00:01.000Z",
      "connectionEpoch": "e1"
    }
  }
]
```

در جهت رفت، `q1` و `q2` اختلاف `10450 - 10125 = 325`، یعنی ۳٫۲۵ واحد تسویه دارند. در جهت برگشت، `q3` هزینهٔ جداگانهٔ ۵ واحد خرد با نرخ ۱/۲ دارد که به ۳ واحد خرد تسویه گرد می‌شود. در `q4` از ۲۰۰ واحد خرد پایهٔ کسرشده، ۱۹۹ وارد معامله و ۱ صرف هزینه می‌شود. مدل ساختگی قیمت‌گذاری ۲۰۰ واحد را بدون آن هزینه معادل ۱۰۳۰۰ و ۱۹۹ واحد را معادل ۱۰۲۴۵ واحد خرد تسویه می‌داند. اختلاف ۵۵ در ۱۰۲۴۵ مستتر است. اختلاف جهت برگشت `10245 - (10000 + 3) = 242` است و کسر دوبارهٔ ۳ یا ۵۵ خطاست.

| نمونه یا تغییر | نتیجهٔ مورد انتظار |
| --- | --- |
| رفت با آستانهٔ ۳۰۰ | فرصت با اختلاف ۳۲۵ |
| برگشت با آستانهٔ ۲۴۱، ۲۴۲ یا ۲۴۳ | به‌ترتیب فرصت، فرصت در مرز برابر، بدون فرصت زیر آستانه |
| پاسخ `q2` با حجم ۲۰۱ و درخواست اصلی ۲۰۰ | `INVALID_QUOTE` برای `q2` |
| جفت `q1` و `q5` با درخواست‌های مستقل ۲۰۰ و ۲۰۱ | `INCOMPARABLE_QUOTES` |
| حذف هزینهٔ `gas` یا صفر کردن مخرج نرخ آن در `q3` | `FEE_UNAVAILABLE` برای `q3` |
| پاسخ با بازار مغایر درخواست، یا جفت با بازار معتبر بیرون رابطه | به‌ترتیب `INVALID_QUOTE` و `INCOMPARABLE_QUOTES` |
| هر دو سمت مطابق درخواست بازنگری پیشین، با بازنگری فعال جدید | `INCOMPARABLE_QUOTES` |
| حذف هم‌ارزی پایه یا نگاشت تسویهٔ رابطه | `ASSET_MISMATCH` |
| سن `q1` برابر ۴۹۹۹ یا ۵۰۰۰ میلی‌ثانیه | به‌ترتیب معتبر و `STALE_QUOTE` |
| سقف اختلاف ۱۰۰۰ یا ۹۹۹ میلی‌ثانیه | به‌ترتیب معتبر و `QUOTE_TIME_SKEW` |
| مشاهدهٔ ۱ میلی‌ثانیه در آینده یا زمان بدون میلی‌ثانیه | `INVALID_QUOTE` |
| قطع بازار B، سپس بازیابی در `00:00:03.000Z` | فقدان صلاحیت کش قبلی. نسل قبلی، replay و مشاهدهٔ پیش از بازیابی رد می‌شوند. مشاهدهٔ سالم `00:00:03.001Z` از نسل جدید با ارزیابی `00:00:03.002Z` صلاحیت را برمی‌گرداند. |
| خطای `{"status":"ERROR","requestId":"q2","sourceId":"sim-b","code":"SOURCE_UNAVAILABLE"}` | همان خطا فقط برای `q2`. برگشت با ورودی‌های معتبر همچنان قابل ارزیابی است. در قطع واقعی، تمام ورودی‌های بازار B متأثرند. |
| همان خطا برای فراخوانی `q3` | `INVALID_QUOTE` برای فراخوانی `q3` |

### مسیر کامل اعتبارسنجی درخواست و پاسخ

اعتبارسنجی مستقل هر دو درخواست و پاسخ پیش از بررسی جفت کامل می‌شود. تولیدکننده فقط درخواست `SIMULATION` را پشتیبانی می‌کند.

| ورودی آزمون | نتیجهٔ مورد انتظار |
| --- | --- |
| درخواست و پاسخ `q1` در `SIMULATION` هستند. درخواست و پاسخ `q2` هر دو در `LIVE` هستند و پاسخ همهٔ فیلدهای درخواست را عیناً بازمی‌گرداند. | `INVALID_QUOTE` برای `q2` به دلیل پشتیبانی‌نشدن `LIVE` در اعتبارسنجی درخواست. بررسی جفت اجرا نمی‌شود. |
| درخواست و پاسخ `q1` در `SIMULATION` هستند. درخواست `q2` در `SIMULATION` است و فقط حالت پاسخ آن به `LIVE` تغییر می‌کند. | `INVALID_QUOTE` برای `q2` به دلیل مغایرت حالت پاسخ با درخواست اصلی. بررسی جفت اجرا نمی‌شود. |

### آزمون واحد داخلی بررسی جفت در مرز مصرف‌کننده

دو Quote فرضیِ ازپیش‌اعتبارسنجی‌شده، A در `SIMULATION` و B در `LIVE`، مستقیماً به تابع بررسی جفت داده می‌شوند. نتیجهٔ مورد انتظار `INCOMPARABLE_QUOTES` است. این ورودی‌های فرضی فقط برای آزمون واحد مرز مصرف‌کننده‌اند. با قرارداد فعلی تولیدکننده که فقط `SIMULATION` را پشتیبانی می‌کند، این حالت در مسیر کامل دست‌یافتنی نیست.

### آزمون جداسازی کش بر اساس حالت

تنها ورودی موجود در کش به `SIMULATION` تعلق دارد و مصرف‌کننده تصمیم `LIVE` می‌خواهد. جست‌وجو با کلید `LIVE` نتیجه‌ای ندارد. دادهٔ `SIMULATION` برای این تصمیم واجد شرایط نیست و فرصتی تولید نمی‌شود.

S1 برای بازنگری ۲ تأیید ۳۷ از ۳۷ گزاره را با داده‌های JSON سند، ساعت ثابت و حساب صحیح گزارش کرد. بازتولید دقیق آن اجرا به برنامهٔ آزمون اصلی و فهرست گزاره‌های آن نیاز دارد. شواهد این سند به طراحی و نمونه‌ها محدودند. اعتبارسنجی اتصال واقعی، مدل قیمت‌گذاری تولید، ساعت همگام، هم‌زمانی و جداسازی عملیاتی پس از پیاده‌سازی موضوع آزمون مستقل‌اند.

## S2 → S3 simulation admission, version 1

S2 validates each leg under the pinned decision context. The admitted
`ExecutionRequest` binds the execution ID, relationship, direction, mapping
revision, fee policy, both market identities and request IDs, base volumes,
profit, threshold, decision time, expiry, mode and status.

The evidence types are defined in `arb_multidex.opportunity.models`.
On each leg, `gross_settlement` includes embedded fees and `settlement` is the
final payment or receipt. `costs` is an immutable tuple in accepted quote order.
Each component records its ID, treatment and settlement atoms. A `SeparateCost`
also carries `asset_id`, `native` (integer units and scale), and the exact
`numerator` and `denominator` used for conversion. For a foreign asset,
`rate_ref` identifies the frozen `rate` observation that S2 checked, including
its trusted source, both assets and scales, fraction, observation and exclusive
expiry. A settlement-asset conversion has a unit ratio and both references are
`None`. The quote source, epoch, observation and age deadline are available as
`market.source_id`, `epoch`, `observed_ms` and `quote_expires_ms`. The effective
leg expiry also accounts for rate expiry. All timestamps are UTC epoch milliseconds.

The detector inserts the versioned canonical payload and the existing unique
claim in one SQLite transaction. S2 owns admission writes. The orchestrator
supplies S3 with the trusted guard for this simulation. Retain the file across
restarts and restrict writes to trusted application code.

- Before a first reservation, call `accepted = guard.verify_pending(request)`.
  A match returns an immutable request reconstructed from the journal. Use
  that returned value for accounting. A missing, changed, resolved or legacy
  ID-only submission returns `None` and cannot authorize a reservation.
- For recovery by ID, `guard.read_pending(execution_id)` returns the same
  typed evidence. Legacy pending IDs remain visible through `guard.pending()`
  for reconciliation. The original evidence cannot be reconstructed from those IDs.
- For a DEX BUY use `accepted.buy`, and for a DEX SELL use `accepted.sell`.
  Book gross settlement and charge each separate native cost once. Embedded
  fees are already reflected in gross settlement. The journal supplies all
  evidence needed from the accepted quote.
- Verification is repeatable while pending. S3 serializes first reservation
  and resolution under its execution-ID idempotency rules, checks freshness
  at reservation, and resolves after reconciliation. For an already journaled
  run, S3 compares the exact request to its own durable record before returning
  its previous result. Resolution permanently spends the S2 claim.
