# Real Decisions API smoke on synthetic fixtures — 2026-10-24

**Completed: 2 requests attempted, 2 succeeded, 2/2 declared fixture labels matched.** This used the real OpenAI Decisions endpoint and two self-authored synthetic generic-card illustrations. It is not real MTG recognition performance, the original-photo benchmark, or a matched Jev comparison. No Jev requests were made.

## Fixtures and frozen question

Public-image retrieval was unavailable, so the explicitly permitted synthetic fallback was used. The reproducible [generator](../mtg_jev/decision_fixtures.py) draws a fictional generic playtest card and a matching background with no card. It uses no MTG artwork, private photo, external image or personal data. PNG inputs are 640x480, unanimated and EXIF-free. No binary input image or raw API response is committed.

The card picture is positive **as a synthetic trading-card depiction**, not a genuine MTG card. The empty background is negative by construction. Both received the same existing `generic_plain` presence question: “Is any trading card printing visible in photo?” No label, expected probability, card count/name, scoring data or provenance was included in the inference document. The threshold was fixed at 0.5 before sending. Card then empty was the frozen order; there were no reruns.

## Actual normalized results

| Fixture | Expected | Returned probability | Prediction at .5 | Correct | Measured elapsed seconds | API-reported input/output tokens |
| --- | --- | ---: | --- | --- | ---: | ---: |
| Synthetic generic card | Present | 1.0 | Present | yes | 1.008397 | 459 / 0 |
| Synthetic empty background | Absent | 0.0 | Absent | yes | 0.226289 | 459 / 0 |

Both responses passed the native model/answer/usage parser. Total API-reported usage was **918 input, 0 output, 0 cache-read and 0 cache-write tokens**. Full local run elapsed time was 1.281401 seconds. Request timing includes transport and response validation, and is not a repeated-trial latency estimate.

The [machine-readable result](decisions-fixture-smoke-results.json) contains only fixture provenance and normalized numeric/status results. The durable book was read back after completion: both slots were complete and no pending/uncertain entry remained. No additional model or billing requests were made.

## Cost, reservation and limits

Reviewed the official [Decisions contract](https://developers.openai.com/api/reference/resources/decisions/methods/create), [endpoint pricing](https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability), and [model limits/rates](https://developers.openai.com/api/docs/models/gpt-6-luna) on 2026-10-24. Requests used `gpt-6-luna` on `/v1/decisions`, one image and one predicate each, with explicit caching and no breakpoints. No Responses or token-count endpoint was used.

The same task-wide **USD 0.50** guard was used, with known prior spend zero. Full-context reservations were USD 0.231/question: 1,050,000 tokens × USD 0.10/M × 2 long-context × 1.10 regional uplift. The whole pair reserved **USD 0.462 before sending**. This was a worst-case reservation, not the expected cost.

| Amount | USD | Evidence |
| --- | ---: | --- |
| Initial reservation | 0.462000000 | Conservative maximum-context preflight |
| Standard-price cost estimate | 0.000091800 | 918 reported input tokens × USD 0.10/M; output free |
| Durable accounted cost upper bound | 0.000100980 | Usage-based estimate including 10% regional uplift |
| Outstanding uncertain reservation | 0.000000000 | Both ledger slots reconciled |
| Observed invoice charge | unknown | API reports tokens, not a currency bill; billing was not queried |

The reservation was reconciled to the usage-based upper bound; the larger initial reservation was not counted as spend. This does not create a fresh USD 0.50 allowance for another run. The two approved slots are consumed and cannot be replayed by the transport. Any later work must retain this spending history and establish its own authorized scope under the same aggregate cap.

## Implementation and offline validation

Added explicit `fixture_smoke` input mode with declared truth and provenance, separate from the original-photo mode. It permits small synthetic/public fixtures without weakening the original-photo JPEG/dimension checks or implying an account balance review. Endpoint pricing review and dated budget preflight remain mandatory. Fixture truth/provenance is frozen for evaluation and never enters the model request.

A PNG decoder issue found by the new tests was fixed in both image-validation paths: metadata inspection can close Pillow's PNG stream, so verification now opens a fresh decoder over the same immutable bytes. No image re-encoding was introduced. Failure diagnostics expose only HTTP status and an allowlisted provider error code; server messages/headers and raw responses are omitted. Failed/uncertain requests still halt without automatic retry.

Final offline checks: **Python 171 tests / JavaScript 6 tests passed**, including 22 stubbed-HTTP transport/accounting tests. New checks cover fixture-mode isolation, truth kept out of inference, PNG validation, correctness/cost/billing separation and allowlisted error diagnostics. CI access was not attempted.

## What this establishes

The real Decisions API accepted image input and returned correctly parsed presence predicates and usage for this easy synthetic positive/negative pair. Two deliberately simple illustrations cannot establish real-card accuracy, specificity on realistic distractors, robustness, repeat stability, localization or identity quality. Bbox and unrestricted card-name output remain outside the inspected native contract. Original-photo benchmarking and matched Jev evaluation remain unperformed.
