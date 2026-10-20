# 2×2 camera wording comparison results — 2026-10-20

[Issue #23](https://github.com/oXyut/mtg-jev-vision-poc/issues/23) / [PR #24](https://github.com/oXyut/mtg-jev-vision-poc/pull/24). [Exact four profiles and frozen protocol](camera-prompt-factorial.md). [Anonymous aggregate JSON](camera-prompt-factorial-results.json). Production wording was not changed.

## Answer

The current multi instructions did not explicitly include overlapping, rotated or object-occluded cards. The optional ranked mode alone said “Include rotated and partly occluded cards.” The experiment compares the exact target `Magic: The Gathering card` against `trading card`, and the presence/absence of one eligibility sentence covering all three physical conditions. The synonym `card used in a card game` was not separately tested.

Neither wording factor produced a repeat-stable improvement on these two photos. Generic+explicit reached all eight aligned cards in 2/4 runs, but only one card in the other two. The difficult board never exceeded 3/8 under any condition. Keep the production default unchanged; generic+explicit is a candidate for a fresh held-out test, not a demonstrated fix. Identification/OCR is outside this experiment.

All 32 planned runs were frozen before scoring. Four repeats per condition/photo, identical 1280×960 EXIF-free camera JPEG bytes, fixed jev-1.14.0, same current multi core, acceptance gates, visible-printed-area AABB rule and call caps. No true count, names or positions entered inference. Provider seed/temperature are not exposed by the adapter and were unset in every condition. Orders were rotated with alternating first photo; provider output remains nondeterministic.

## Adopted outputs

TP is unique truth cards under maximum-cardinality one-to-one IoU matching; FP includes duplicate predictions. Values after the sequence are per-run means. A is the difficult board, B the aligned photo; each has eight scoring targets. The sequence follows repeats 1→4, not execution order. An error frame keeps its partial accepted output and is not excluded.

| Photo | Wording | TP @.50 (r1–r4) | TP @.75 (r1–r4) | Mean FP/FN @.50 | Mean seconds [min,max] | Mean HTTP | Mean estimated US$ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | MTG・明示なし | 1, 0, 1, 2 | 1, 0, 1, 2 | 0.00/7.00 | 2.755 [1.259,3.514] | 4.50 | 0.000401373 |
| A | MTG・明示あり | 1, 1, 1, 1 | 1, 1, 1, 1 | 0.00/7.00 | 2.547 [2.167,2.792] | 4.00 | 0.000404166 |
| A | 汎用・明示なし | 1, 3, 1, 1 | 1, 3, 1, 1 | 0.00/6.50 | 3.107 [2.580,4.487] | 5.00 | 0.000485300 |
| A | 汎用・明示あり | 1, 1, 2, 1 | 1, 1, 2, 1 | 0.00/6.75 | 3.154 [2.503,4.780] | 5.25 | 0.000488565 |
| B | MTG・明示なし | 2, 7, 3, 2 | 2, 6, 3, 2 | 0.25/4.50 | 5.797 [3.840,10.292] | 11.75 | 0.000965958 |
| B | MTG・明示あり | 2, 2, 3, 2 | 1, 2, 3, 2 | 0.00/5.75 | 4.006 [3.319,4.918] | 6.25 | 0.000629969 |
| B | 汎用・明示なし | 3, 3, 2, 3 | 3, 3, 2, 3 | 0.00/5.25 | 5.214 [3.772,5.823] | 8.25 | 0.000750404 |
| B | 汎用・明示あり | 8, 1, 8, 1 | 8, 1, 8, 1 | 0.00/3.50 | 5.578 [2.324,8.788] | 11.25 | 0.001058453 |

Mean matched IoU @.50 (mean across nonempty runs): A MTG/plain .912, MTG/explicit .895, generic/plain .880, generic/explicit .908; B .848/.838/.862/.830 respectively. These IoUs describe TP geometry and do not include missed cards.

The MTG/plain aligned run with eight adopted boxes had seven unique TP, one duplicate FP (IoU .50), and one FN. At .75 it had six TP. An adopted count of eight is not eight-card detection success. Generic/explicit aligned r1 and r3 had eight TP at both thresholds, zero FP/FN; r2 and r4 each had one TP.

## Factor differences and stability

For each factor, pair runs of the same repeat/photo under the other factor. Four repeats × two settings gives eight pairs per photo. These are descriptive within-photo differences, not independent held-out samples or a significance claim.

| Photo | Factor difference | Mean unique TP difference @.50 | Range | Wins/ties/losses |
| --- | --- | --- | --- | --- |
| A | generic-minus-mtg | +0.375 | [-1,+3] | 2/5/1 |
| A | explicit-minus-plain | -0.125 | [-2,+1] | 2/4/2 |
| B | generic-minus-mtg | +0.750 | [-4,+6] | 4/0/4 |
| B | explicit-minus-plain | +0.250 | [-5,+6] | 2/3/3 |

The generic target averaged a small positive difference, but aligned pairs split four wins/four losses. Explicit eligibility averaged −.125 on the board and +.250 aligned, with losses and ties as well as wins. The three eligibility clauses are one bundled factor; these photos mix overlap, rotation, dice, glare and cropping. This does not identify the separate causal effect of any one physical condition or clause.

## Raw valid Box proposals versus adoption

Raw search = every valid Box returned on a full masked-search image, including low-presence responses. Raw crop = every valid Box from the actual local verification calls, mapped back to original coordinates. They are separate adaptive stages, not exhaustive predictions with equal proposal counts. Zero-area/malformed Boxes have no defined IoU and are counted separately below. FP already includes duplicates; do not add the duplicate count to FP again.

Each cell is a four-run mean. `dup` is excess predictions eligible for a truth match beyond unique TP. `geom groups/pairs` uses prediction-to-prediction IoU .85 connected groups/pairs and is distinct from true physical-card identity.

| Photo | Wording | Stage | Proposals | TP/FP/FN @.50 | TP/FP/FN @.75 | dup @.50 | geom groups/pairs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | MTG・明示なし | search_raw | 2.00 | 1.50/0.50/6.50 | 1.25/0.75/6.75 | 0.00 | 2.00/0.00 |
| A | MTG・明示なし | crop_raw | 2.50 | 1.75/0.75/6.25 | 1.75/0.75/6.25 | 0.25 | 2.25/0.25 |
| A | MTG・明示なし | adopted | 1.00 | 1.00/0.00/7.00 | 1.00/0.00/7.00 | 0.00 | 1.00/0.00 |
| A | MTG・明示あり | search_raw | 2.00 | 1.00/1.00/7.00 | 1.00/1.00/7.00 | 0.00 | 2.00/0.00 |
| A | MTG・明示あり | crop_raw | 2.00 | 1.00/1.00/7.00 | 1.00/1.00/7.00 | 0.75 | 2.00/0.00 |
| A | MTG・明示あり | adopted | 1.00 | 1.00/0.00/7.00 | 1.00/0.00/7.00 | 0.00 | 1.00/0.00 |
| A | 汎用・明示なし | search_raw | 2.50 | 1.50/1.00/6.50 | 1.50/1.00/6.50 | 0.00 | 2.50/0.00 |
| A | 汎用・明示なし | crop_raw | 2.50 | 1.50/1.00/6.50 | 1.50/1.00/6.50 | 0.50 | 2.50/0.00 |
| A | 汎用・明示なし | adopted | 1.50 | 1.50/0.00/6.50 | 1.50/0.00/6.50 | 0.00 | 1.50/0.00 |
| A | 汎用・明示あり | search_raw | 2.25 | 1.50/0.75/6.50 | 1.25/1.00/6.75 | 0.00 | 2.25/0.00 |
| A | 汎用・明示あり | crop_raw | 3.00 | 1.50/1.50/6.50 | 1.50/1.50/6.50 | 1.50 | 2.75/0.25 |
| A | 汎用・明示あり | adopted | 1.25 | 1.25/0.00/6.75 | 1.25/0.00/6.75 | 0.00 | 1.25/0.00 |
| B | MTG・明示なし | search_raw | 4.75 | 3.50/1.25/4.50 | 3.25/1.50/4.75 | 0.00 | 4.75/0.00 |
| B | MTG・明示なし | crop_raw | 7.00 | 3.50/3.50/4.50 | 3.25/3.75/4.75 | 1.75 | 6.25/0.75 |
| B | MTG・明示なし | adopted | 3.75 | 3.50/0.25/4.50 | 3.25/0.50/4.75 | 0.25 | 3.75/0.00 |
| B | MTG・明示あり | search_raw | 3.00 | 2.25/0.75/5.75 | 2.25/0.75/5.75 | 0.00 | 3.00/0.00 |
| B | MTG・明示あり | crop_raw | 3.00 | 2.25/0.75/5.75 | 2.00/1.00/6.00 | 0.25 | 3.00/0.00 |
| B | MTG・明示あり | adopted | 2.25 | 2.25/0.00/5.75 | 2.00/0.25/6.00 | 0.00 | 2.25/0.00 |
| B | 汎用・明示なし | search_raw | 3.75 | 2.50/1.25/5.50 | 2.50/1.25/5.50 | 0.25 | 3.75/0.00 |
| B | 汎用・明示なし | crop_raw | 4.50 | 2.75/1.75/5.25 | 2.75/1.75/5.25 | 0.75 | 4.00/0.50 |
| B | 汎用・明示なし | adopted | 2.75 | 2.75/0.00/5.25 | 2.75/0.00/5.25 | 0.00 | 2.75/0.00 |
| B | 汎用・明示あり | search_raw | 5.25 | 4.50/0.75/3.50 | 4.50/0.75/3.50 | 0.00 | 5.25/0.00 |
| B | 汎用・明示あり | crop_raw | 5.75 | 4.50/1.25/3.50 | 4.50/1.25/3.50 | 0.00 | 5.75/0.00 |
| B | 汎用・明示あり | adopted | 4.50 | 4.50/0.00/3.50 | 4.50/0.00/3.50 | 0.00 | 4.50/0.00 |

## Errors and continuation

Two full-image search responses contained a zero-area Box: B MTG/explicit r1 and B generic/explicit r2, one invalid raw search Box in each. Both HTTP responses had valid usage; the detector stopped those frames and retained two/one accepted cards respectively. They were not retried, replaced or removed. There were no HTTP failures, unknown usage, pending or automatic retries.

After the first eight-run block, the initial harness stopped the entire phase on the first malformed answer. Offline replay confirmed the geometry error without an API call. A pre-scoring Issue addendum allowed only the unexecuted planned prefix to continue after ledger/protocol verification, deducting prior spend/calls from the original phase limits. This orchestration correction did not alter the detector or prompts; all four complete repeats are included.

## Missed-card features (evaluation only)

Board: the portrait card under a die was adopted in 15/16 runs; the sideways card under another die with strong glare in 1/16; the sideways dark card in 3/16. The thin horizontal exposed strip, three narrow vertical fan strips, and the front card clipped at the right image boundary were each adopted 0/16. The latter four fan cards never appeared as a valid matched raw search proposal. Therefore these misses cannot all be attributed to hard visual content: early greedy stopping also prevents later search. The exposed horizontal strip appeared as a raw search TP in 2/16 runs but never survived adoption.

Aligned: the upper-left card was adopted 16/16; lower-left 14/16; upper-second 8/16; upper-third/upper-right/lower-second/lower-third 3/16 each; lower-right 2/16. Unoccluded full cards were also missed. Frequent stopping involved single-card ambiguity, insufficient new unmasked area, low Box/crop agreement and occasionally unresolved crop boundary. Returned crops can reselect an already accepted physical card. These observations are descriptive, not separate causal estimates of glare/rotation/occlusion.

Truth was fixed to the existing corrected board annotation (audit v2) and existing aligned annotation before this experiment. The board audit had corrected earlier transcription errors in a previous phase; no annotation or bbox rule changed after this comparison. Existing boundary uncertainty is retained; hidden whole-card extents are never substituted to improve IoU.

## Recommendation, latency and accounting

Do not promote generic or explicit wording to a production improvement from this sample. Keep the existing wording and multi as manual single-frame development confirmation. The 1.5-second live-overlay TTL is exceeded by almost all results (mean 2.5–5.8 seconds; maximum 10.292 seconds). Lower latency on a run that stops after one card is not better detection throughput. Even successful aligned generic/explicit runs take 8.727/8.788 seconds and 19 HTTP each. Board success is still unmet.

Next, if another experiment is authorized, test the fixed candidate on new held-out images with equal repeats, and isolate clauses or separate physical factors only in a new protocol. First investigate mask/crop physical-card correspondence and premature stopping; do not keep tuning wording against the same two photos. No additional experiment was run.

This phase: 225 actual HTTP, input493,732/output10,036 tokens, estimated US$0.020736744 of the US$0.05 cap, phase call cap384, 32/32 runs. Known Cloud cumulative: 508 HTTP, input1,173,465/output29,484 tokens, estimated US$0.049285530. Mac-local usage and total free balance are unknown; no new approval barrier was imposed. No payment/autorecharge settings changed. Fixed endpoint/model/header/proxy and redirect refusal were maintained.

The private report contains the two photos and all 32 accepted/raw overlays, scoring IDs and missed-card features; it is under5MB and saved separately to the user's Library. Public files contain no photos/crops/EXIF/truth or predicted coordinates/raw API bodies/private Library identifiers. Mac hardware/Safari and identification/OCR were not part of this static wording experiment.
