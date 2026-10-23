# OpenAI Decisions / Jev static-photo comparison — Issue #25

## Outcome (2026-10-23)

**The requested direct bbox comparison is not run.** The user confirmed OpenAI as the provider. The GA announcement, current reference, and two current official SDKs agree on predicate/choice/score output, with refusal as an additional answer outcome. They contain no native bbox or unrestricted card-name output contract. This is a documented capability limitation, not a measured recognition failure. No Responses API substitution, grid-coordinate choices, external detector, or image-as-text workaround was used.

| Work | Status | Evidence / limit |
| --- | --- | --- |
| Exact vendor | passed | User clarification: OpenAI |
| GA / reference / SDK review | passed | Pinned sources below; checked after the user's GA/Box question |
| Native bbox requirement | failed (unsupported contract) | No Box in the current request/answer unions; no live request made |
| Offline image-predicate adapter and evaluation tests | passed | Synthetic evidence only; commands below |
| Original private input recovery | failed | Both supported Library transfers failed; one authorized fresh-preparation retry of the smaller report also failed; no local report bytes installed |
| Photo byte count / original experiment hash / fixed truth verification | not run | No recovered files; do not use report-size metadata as byte verification |
| Live Decisions recognition, latency, usage, billing | not run | 0 model HTTP, $0 new model spend; no separately approved budget or confirmed account credit |
| Real-photo identity validation | not run | Existing Jev work did not complete it; no candidate catalog was invented |

Base: [PR #24](https://github.com/oXyut/mtg-jev-vision-poc/pull/24), SHA `9746cb5499543f1cc1e5a6767d9dbfeeec1216c5`. New branch `experiment/decision-api-comparison`, [Issue #25](https://github.com/oXyut/mtg-jev-vision-poc/issues/25). Existing production prompts, detection gates and transport are unchanged. No merge or deployment.

## Current official contract and the GA Box check

The [October 22 GA announcement](https://openai.com/index/decisions-api/) lists three decision answer types. Its “Box Extract” example is document routing by **Box the company**; it is not a bounding-box feature. The [October 22 API changelog](https://developers.openai.com/api/docs/changelog) describes caching, question-local images, native JSON input, and an increase to 400 questions, without announcing Box output.

The [current create reference](https://developers.openai.com/api/reference/resources/decisions/methods/create) specifies `POST https://api.openai.com/v1/decisions`, model `gpt-6-luna`, Bearer authentication via `OPENAI_API_KEY`, and image parts using data URLs or public image URLs. Its question union is predicate/choice/score; answers add refusal. Finite choices do not provide arbitrary card-name generation. JSON is an input representation, not arbitrary output. Accordingly, native bbox regression and unrestricted identity are unsupported **in the inspected public contract**, rather than claimed impossible in every future API version.

SDK cross-check, independent of the guide:

| Official SDK source | Pinned revision | Observed types |
| --- | --- | --- |
| [Python request types](https://github.com/openai/openai-python/blob/81792ff040a2ca2b70348163049d3d69d5ba95cf/src/openai/types/decision_create_params.py) | `81792ff040a2ca2b70348163049d3d69d5ba95cf` | `Question` union has Predicate, Choice, Score; no Box |
| [Python answer types](https://github.com/openai/openai-python/blob/81792ff040a2ca2b70348163049d3d69d5ba95cf/src/openai/types/decision.py) | same | `Answer` union adds Refusal; no Box |
| [Python v3.37.0 changelog](https://github.com/openai/openai-python/blob/81792ff040a2ca2b70348163049d3d69d5ba95cf/CHANGELOG.md) | release 2026-10-22 | Expanded Decisions inputs and question instructions |
| [TypeScript request and answer types](https://github.com/openai/openai-node/blob/4c11b87c3e5e4fcddeeffc85b55502b2296f49fd/src/resources/decisions.ts) | `4c11b87c3e5e4fcddeeffc85b55502b2296f49fd` | Same three question / four answer variants |

No OpenAI SDK was installed in the execution environment. The above public sources were read without installing a runtime dependency. The machine-readable [spec evidence](decision-api-spec-evidence.json) records the source revisions and content blob SHAs.

## Implementation and fair scope

`mtg_jev.decisions` is an **offline contract adapter**, not a live transport. It builds native `input_image` documents for full-image presence or externally supplied crop verification, retaining the original bytes; base64 is used only as the documented image-part encoding. It parses native predicate/refusal answers and known token usage. It rejects bbox/open-name/grid tasks before inspecting an image. It has no key lookup or HTTP client. The two diagnostics are kept distinct; a refusal is not mapped to “card absent.” There is no cost estimator that silently prices cache writes as uncached input.

`mtg_jev.comparison` and `experiments/compare_static_providers.py` supply API-free plan/freeze/score operations. The 64-run schedule is a **non-executable reference design** (2 providers × 2 photos × 4 wording cells × 4 repeats), not approval to send requests. Native Decisions live bbox records are explicitly rejected. This lets synthetic records validate the evaluator without fabricating a real comparison.

For a future compatible detector, hold the two approved EXIF-free 1280×960 JPEG byte streams, fixed corrected board/aligned truth, model version, original visible-print AABB rule, capacity16 and existing Jev crop/mask/recenter/acceptance settings fixed. `make_plan` retains the exact five prompt profiles from `prompt_factorial.py`, including MTG versus `trading card` and the same bundled overlap/rotation/object-occlusion sentence. Provider order alternates within the existing balanced condition schedule. No count/name/position hints may enter inference. Current mixed scenes cannot isolate causal effects of overlap, rotation or occlusion independently.

The private input manifest hashes original bytes without re-encoding and hashes canonical truth JSON. It does not itself prove that supplied images are approved: independently compare its image hashes against the retained original experiment manifest before any new inference. JPEG/size/EXIF checks use the original file, before metadata-stripping preparation. The manifest remains blocked after freezing; the CLI has no live option.

Scoring requires an exact saved schedule prefix, manifest/image binding, and unchanged truth/profile/settings hashes. Retain error frames and partial valid detections. Score adopted, optional raw-search and raw-crop layers separately at IoU .50/.75 using the existing maximum-cardinality one-to-one matcher. FP already includes duplicates; report excess truth-eligible predictions and geometric duplicate pairs separately. Malformed model boxes have no IoU: record their counts with an error frame, never synthesize geometry. Pending/uncertain outcomes stop the prefix; unknown cost remains null. Each row includes latency/attempted HTTP/cost, with known/unknown counts and mean/range in cell aggregates. Only complete balanced repeat blocks receive paired comparisons; partial rows remain visible. Status `complete` means all planned records exist, not that recognition succeeded.

Identification uses the existing `evaluate_identifications` after adopted bbox matching. It reports matched-known coverage counts, accepted/wrong/correct, unknown rate, matched top1, and end-to-end name recall/precision at each IoU. A named localization FP counts as wrong. Truth requires explicit identity IDs or null; missing identity work remains `not_run`. The existing scorer's deterministic geometric assignment handles IoU ties; identity never changes localization matching.

Full-frame presence cannot supply TP/FP/FN for individual-card bbox detection. Jev-generated crops would make a Decisions verifier dependent on Jev's upstream recall. Truth crops are oracle classification diagnostics and must not be labeled end-to-end detection. A shared, independently frozen crop/proposal set can compare verifiers, but changes the task and needs a separate protocol and private dataset approval. Finite identity choices need a separately justified, frozen candidate catalog and unknown option, never a list copied from the scoring truth. A grid-choice detector would change localization resolution, search strategy and HTTP cost; none was implemented or evaluated here.

## Authentication, free tier and smallest useful live proposal

Tracked repository configuration defines only the existing TypeSafe transport. Shell variable-existence checks (without expanding values) found `OPENAI_API_KEY` absent and `HTTPS_PROXY` present in this execution environment. This does **not** establish whether a managed OpenAI network-secret route exists: no per-domain mapping metadata or environment-inspection action is exposed here, and the workspace's environment metadata directories are empty. The baseline code/configuration documents only the TypeSafe mapping. The existing `api.openai.com` route is therefore **unknown**, rather than declared absent. No secret value was searched, read or copied; no key was created, no access/payment settings changed, and the TypeSafe placeholder was not reused for OpenAI. Account-specific free credit remains unknown. The [Decisions pricing guide](https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability) publishes $0.10 per million uncached input tokens and no output charge; caching and regional/long-context pricing can change the bill. No unconditional free allocation was established. Existing Jev spend is unrelated authorization.

Safe next setup check: in the existing PoC Cloud environment's settings, inspect only the existing route's name and allowed host mapping for `api.openai.com`; select an already configured environment if one exists, then check placeholder presence without expanding it. The environment's human-readable name and OpenAI mapping are not supplied to this execution, so no name is invented. Do not create a credential, add a route, change permissions, copy values, or test authentication with an API call under the current authorization. If no existing route can be verified, report that setup prerequisite separately. A verified route still needs spending approval before use.

Proposed first live diagnostic, **pending approval and private input verification**: 2 HTTP maximum, one full-image MTG/plain presence predicate per approved photo, no crop or identity inference, no retries. It checks transport/image/refusal behavior only; both photos contain cards, so it has no negative-scene specificity evidence and cannot establish detection accuracy or repeat stability.

For a conservative proposal, reserve **US$0.50 maximum** for these 2 HTTP. The [current Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna) lists a 1,050,000-token context and a 2× input multiplier above272K; with uncached input, no cache breakpoints, global standard processing, the deliberately pessimistic calculation is `2 × 1,050,000 × $0.10/1M × 2 = $0.42`. A future live transport must reserve the per-request worst case before sending, reject an unconfirmed pricing/processing configuration, account every attempt durably, halt on unknown usage, and never retry automatically. This is an upper-bound planning assumption using the published model context, not a measured token count or a guarantee of account free credit. A lower cap needs a verified tighter token bound. The current code does not execute this proposal. A balanced four-profile presence diagnostic would instead require8HTTP (one repeat/photo/cell) and a distinct approval; it still would not produce bboxes.

## Private-data recovery and blockers

The environment initially contained only the nearly empty main checkout; no private input path was supplied. After the two existing private reports were identified, supported Library materialization was attempted once for each. Preparation succeeded, but the transfer helper reported `library file transfer failed: download failed` (exit1) for both, and neither report was installed locally. After explicit authorization to recover the smaller report, the known workspace materialization locations were checked for existing copies/duplicates (none found). Fresh preparation succeeded for the smaller report, then exactly one bounded retry with the same official consumer-local transfer helper failed with the same exit1/error. No report was installed; report byte/hash and embedded photo/truth verification remain **not run**. No HTTP status or explicit access-denial reason was provided, and no model invocation has an uncertain outcome. No alternate transfer route or further retry was used. Expected report sizes from metadata do not establish local byte counts. The separate GitHub Actions listing was denied with `Forbidden`; it was not retried or bypassed, so remote CI remains unverified.

Needed to resume: the original approved 1280×960 EXIF-free JPEG pair; retained original SHA256 manifest; the corrected board audit-v2 and aligned fixed bbox truth, with identity ground truth if available; and any saved Jev prediction/usage records needed for replay. Embedded report images may be reused only by extracting their original bytes and matching the experiment hashes, never by screenshotting or recompressing. An existing authenticated OpenAI route with confirmed credit/pricing and explicit request/cost approval is also needed for live diagnostics. Apple Photos and photo libraries were not accessed. No new private dataset was obtained or uploaded.

## Reproduction and validation

From this branch with the project's existing dependencies installed:

```bash
python -m unittest discover -s tests -v
node --test tests/camera-core.test.mjs
python experiments/compare_static_providers.py plan --output /tmp/decision-plan.json
```

The output path must not already exist; a private manifest cannot be written under the public checkout. The default plan is API-free, has missing inputs, and remains `not_run`.

Only after approved private files have been made available, use absolute paths outside this checkout:

```bash
python experiments/compare_static_providers.py plan --repeats 4 \
  --scene-a /private/scene-A.jpg --scene-b /private/scene-B.jpg \
  --truth-a /private/scene-A.truth.json --truth-b /private/scene-B.truth.json \
  --output /private/comparison-manifest.json
python experiments/compare_static_providers.py score \
  --manifest /private/comparison-manifest.json --records /private/normalized-runs.json \
  --truth-a /private/scene-A.truth.json --truth-b /private/scene-B.truth.json \
  --output /private/comparison-scores.json
```

Truth interchange shape is `coordinate_system`, `image:{width:1280,height:960}`, and `objects:[{box,card_id}]`; `box` uses normalized original xyxy edges. Preserve the existing annotation rule; do not relabel to improve a score. Canonical JSON SHA256 uses UTF-8, sorted keys, compact separators, no NaN. Each private normalized run contains its planned provider/scene/condition/repeat, `manifest_sha256`, `image_sha256`, `evidence` (live/synthetic/mock_replay/stubbed_http), `status` (complete/error), `identity_status` (complete/not_run), `request_outcome` (known/pending/uncertain), `seconds`, attempted `http`, `cost_usd` (null if unknown), adopted `detections`, optional raw layers and `invalid_boxes` counts. Identity outcomes use the existing `identification` shape. All executed records must be saved before the separate scorer opens truth. No current producer emits live Decisions bbox records.

Validation: **149 Python tests and 6 JavaScript tests passed**, including all137 existing Python tests and12 new tests. `git diff --check` and the API-free plan command passed. The 12 new tests generate generic synthetic rectangles and native response stubs in temporary storage. They cover image-byte preservation/EXIF rejection, absence of network/key lookup, refusal/invalid answer handling, unsupported tasks, one-to-one matching at both thresholds, duplication, identity FP/unknown outcomes, failure retention, unknown costs, complete-block pairing, evidence separation, drift/rerun rejection, safe aggregate output, overwrite protection and the CLI freeze→score path. Synthetic success is evidence about the adapter/evaluator only. [Machine-readable validation record](decision-api-validation.json).

Historical Jev evidence remains [the existing 32-run report](camera-prompt-factorial-results.md): board max3/8, unstable aligned8/8, 225HTTP and estimated$0.020736744 for that phase. It was not rerun or scored from private data here. No new provider accuracy ranking is justified.
