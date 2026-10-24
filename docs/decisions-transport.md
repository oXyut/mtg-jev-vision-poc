# Decisions transport and USD 0.50 gate — Issue #25

Status, 2026-10-24: the real Decisions API smoke on two self-authored synthetic generic-card fixtures completed successfully. Both declared fixture labels matched; actual usage/latency and estimated cost are in [the smoke report](decisions-fixture-smoke.md). This is separate from the unperformed original-photo benchmark and matched Jev comparison. The original task-wide USD 0.50 cap was used, with no payment, account or permission changes.

## Contract and pricing evidence

Reviewed the official [create reference](https://developers.openai.com/api/reference/resources/decisions/methods/create), [Decisions guide](https://developers.openai.com/api/docs/guides/decisions), [Luna model limits](https://developers.openai.com/api/docs/models/gpt-6-luna), and [pricing](https://developers.openai.com/api/docs/pricing) on 2026-10-24. The endpoint is `POST https://api.openai.com/v1/decisions`, model `gpt-6-luna`. Questions support predicate/choice/score; answers also support refusal. There is no native bbox or unrestricted card-name output. This transport implements only the existing presence and fixed-crop predicates; it does not call Responses or imitate a detector.

The guide prices uncached input at USD 0.10/M and output at zero. The model has a 1,050,000-token context, a 128,000-token output limit, a 2x input multiplier above 272,000 input tokens, and a 10% regional uplift. The request explicitly disables caching with no breakpoints. Nonzero cache usage, inconsistent counters or limits outside the snapshot cause a halt. No token-count endpoint is needed or called.

The snapshot ID is `decisions-luna-2026-10-24-standard-no-cache`. Live preflight rejects a different ID or a later UTC date. Before later use, review the official contract, context limits and rates and update the snapshot and its tests. The original-photo mode retains its account-review attestation. The separately authorized fixture-smoke mode requires review of the documented standard Decisions endpoint rates and does not claim an account balance or invoice review. No unsupported processing-tier field is added to the API request.

## Aggregate budget guarantee and scope

Amounts use integer nanodollars. For each question the gate reserves the **entire** model context at the highest documented input multiplier and regional uplift:

`1,050,000 × USD 0.10 / 1,000,000 × 2 × 1.10 = USD 0.231`.

Reservation is per question, conservatively allowing shared image/input tokens to be charged repeatedly. A presence request has one question; a crop-verification request has two. No smaller image-token estimate or expected token usage is used as a bound.

| Frozen plan, no prior comparison spend | Maximum reserved | Result |
| --- | ---: | --- |
| Two full-image presence requests | USD 0.462 | Within cap |
| One fixed-crop request, two predicates | USD 0.462 | Within cap |
| Two fixed-crop requests | USD 0.924 | Rejected before any send |

The gate adds **all known prior spend for this comparison not already recorded in this book**, including other providers, and reserves every remaining planned request before allowing the plan. Unknown prior spend is rejected. The prior amount can only increase on reopening. Historical spend from other Jev experiments is not falsely treated as spending for this new comparison. This runner makes no new Jev calls.

Every invocation must share the same canonical private ledger for this one comparison. Exclusive creation refuses an existing book. A process-level `flock` is held throughout the run; a thread lock serializes calls. A reservation is written atomically and both file and directory are fsynced **before** entering HTTP. Files have mode 0600, and symlink/shared-permission/hardlink ledgers are rejected. The ledger binds the frozen approval, request bodies, request count (at most two), pricing snapshot, prior spend and completed prefix. Changes, omissions and charged-slot replays are rejected.

Valid usage is reconciled at a conservative cost upper bound, retaining the regional premium and the applicable long-context multiplier. Known usage is recorded even if answers are malformed, then execution halts. These are usage-derived upper bounds, **not observed invoice charges**. Unknown usage, HTTP errors, redirects, timeouts, cancellation during HTTP, process crashes and failed persistence retain the reservation and block continuation. No automatic retry or automatic uncertain-request release exists. Cancellation known to occur before sending has zero HTTP/cost and still stops the run. A complete prefix can resume only its unexecuted planned suffix; no completed slot is resent.

Under the reviewed provider contract/rates, an intact shared ledger and truthful prior-spend declaration, prior spend plus accounted spend plus outstanding reservations cannot exceed USD 0.50. This local gate does not set an organization/account billing limit and cannot protect against owner deletion/tampering, a second independently initialized book, unrelated clients, undisclosed external charges or a provider violating its documented limits/prices. It never changes payment settings. If those assumptions cannot be established, do not enable live execution.

## Approved private inputs and operation

`FrozenVerification` reads only explicitly listed absolute private paths outside the checkout. It requires two distinct original EXIF-free 1280x960 JPEGs and verifies their exact approved SHA256 values before building requests. Bytes are never recompressed. Fixed crops additionally require separate approval and exact crop hashes. No truth, coordinates, arbitrary prompt, external image URL, alternate host/model or live bbox record is accepted.

The approval JSON is private and must contain:

| Field | Meaning |
| --- | --- |
| `schema` | Integer 1 |
| `images_approved` | Explicit `true` attestation for this exact original pair |
| `request_count_approved` | Integer equal to the frozen number of runs, 1 or 2 |
| `pricing_id` | The reviewed snapshot ID above |
| `standard_pricing_and_funding_reviewed` | Explicit `true` after account-side review; not an API probe |
| `originals` | Two records, ordered scene-A/scene-B, each with absolute `path` and original-byte `sha256` |
| `runs` | Ordered records with `source` (0 or 1), `task` (`presence` or `crop_verification`), `condition` (one of the four existing factorial profiles) |
| `fixed_crop_set_approved` | Required `true` for crop runs |
| crop-run `crop` / `crop_origin` | Exact `{path, sha256}` record and `independent`, `jev`, or `truth_oracle` provenance |
| `historical_manifest` | Optional retained original experiment JSON; its `inputs.scene-A/scene-B.sha256` must match the pair |

Consent/provenance are operator attestations bound to exact bytes; code cannot infer them from an image. With no retained historical manifest, results explicitly say `fresh_evaluation_historical_match_unverified`. Matching that manifest proves original-byte identity only, not recovery of historical truth or per-request predictions. Crop provenance remains in private results. Jev crops depend on upstream Jev recall; truth crops are oracle diagnostics. Neither is end-to-end detection. Both original scenes contain cards, so presence on this pair does not establish specificity or detection accuracy. The transport does not implement bbox/name scoring or a fair new Jev crop experiment.

After inputs are available, the following commands are **offline**; use one private canonical book and a known prior amount, never a new book to evade consumed budget:

```bash
python experiments/run_decisions_verification.py init-budget \
  --manifest /private/approved-verification.json \
  --ledger /private/issue-25-budget.json --prior-spend-usd 0
python experiments/run_decisions_verification.py preflight \
  --manifest /private/approved-verification.json \
  --ledger /private/issue-25-budget.json --prior-spend-usd 0
```

Initialization alone can preserve a historical snapshot; `preflight` and live execution require a current snapshot. `run` additionally requires both `--approve-live` and `MTG_DECISIONS_ENABLE_LIVE=1`. Those opt-ins were used only for the two recorded synthetic-fixture requests. The key is read only after input, pricing, budget and opt-in checks. It is treated as an opaque `OPENAI_API_KEY` placeholder and sent only in Bearer to the fixed HTTPS endpoint. Default proxy handling and TLS validation remain enabled. All redirects are refused and there is no proxy bypass or retry fallback. Error responses are parsed only for an allowlisted provider error code; messages, headers and raw bodies are never emitted.

The private ledger atomically stores only normalized predicates/refusals, token counters, timing, state and cost bounds alongside its private binding. It contains no credential, image, coordinate, original path or raw response. Console output contains fixed status labels/counts/preflight cost bounds, never predictions or identifying bindings. All caught errors are redacted. A cancelled event is checked before/after each request; a blocked synchronous HTTP operation can finish or hit its socket inactivity timeout (at most 30 seconds) before cancellation is observed. There is no background worker that can outlive accounting, no immediate remote-cancellation promise, and no strict wall-clock timeout guarantee.

## Historical artifacts and remaining stages

Read-only examination of every locally available commit's tracked artifact paths found only the existing aggregate result JSON and Decisions documentation records. The [32-run Jev aggregate](camera-prompt-factorial-results.json) preserves phase token/cost/count summaries, including 225 HTTP, 493,732 input and 10,036 output tokens. It contains no original image hashes or individual bbox coordinates. No retained original image manifest, corrected bbox truth, per-request Jev response/usage or individual predictions were recovered. No private-storage search, Library retry, photo-library access, CI listing or route around the previous `Forbidden` was performed.

- **Passed:** offline transport/accounting tests and complete local Python/JS suites; see [validation](decision-api-validation.json).
- **Failed capability requirement:** native bbox/free-name output is absent from the inspected Decisions contract. This is not a recognition result.
- **Not run:** original-byte/hash matching, fixed truth recovery, matched historical replay, real presence/crop recognition, real latency/usage/billing or identity evaluation. Without historical artifacts, any later evaluation must be labeled fresh unless its matching evidence is actually recovered.
- **Pending live prerequisites:** the two approved original files and private approval; a current confirmed pricing/funding configuration; explicit request-count/opt-in; the single durable budget book with known prior spend. Fixed crops need their own approved set/provenance. Exact bbox/identity comparisons remain unsupported.

Earlier Library transfers exhausted their bounded retry and remote Actions listing was denied `Forbidden`; these actions remain unattempted here. The separate fixture smoke made **2** real Decisions image requests; its usage-based cost estimate is **USD 0.000091800**, with invoice billing unmeasured. No merge/deploy.

## Explicit fixture-smoke mode

`evaluation_kind: fixture_smoke` is separate from the default original-photo mode. It requires `standard_decisions_pricing_reviewed: true`, two boolean `fixture_truth` labels and two `fixture_provenance` records (`kind` synthetic/public and a description). Only presence runs are accepted. It allows smaller EXIF-free still PNG/JPEG fixtures, without changing original-photo JPEG/dimension requirements. Truth and provenance never enter inference. Self-authored fixtures can be prepared offline with `mtg_jev.decision_fixtures.prepare`; it refuses overwrites. The completed synthetic smoke and spent/remaining reservations are recorded separately.
