# Camera prompt factorial: pre-registered protocol (Issue #23)

## Goal / authorization
Compare only target wording (Magic: The Gathering card vs trading card) and explicit eligibility under overlap/rotation/object occlusion, in a 2×2 factorial on the two previously authorized private real photos. No camera operation, no new hosts, no payment setting changes. Current baseline is commit 1b0261e896e76ab8d928fd5133ce3c02f0f50dd2 on PR21. Current multi has no explicit overlap/rotation/object-occlusion instruction; optional ranked mode alone mentions rotated/partly occluded cards.

## Frozen protocol before live calls
- Current multi algorithm: same detect_masked core, recenter=True, search_caption=short, verify_uncertain_proposals=True. Whole-frame p<=.20 stops; other proposals go through the same crop checks.
- Same source bytes per photo: existing oriented, EXIF-free 1280×960 JPEG, quality .82 produced by browser canvas. Verify retained SHA256 manifest. No truth-derived crops/masks.
- Fixed model jev-1.14.0, fixed endpoint/header/proxy/no redirects/no retries. No provider RNG/temperature/seed field is supported by this adapter; leave all unset for every condition. Deterministic balanced run order only, no content-dependent ordering.
- Capacity16, per-frame64 actual HTTP; phase maximum384 HTTP including failures, estimated additional spend<=US$0.05. Same caps for every condition; metadata ledger before every HTTP. Stop on insufficient free balance, provider/usage error, uncertain outcome, call cap or conservative budget reservation. Never retry automatically.
- Plan 4 repeats per condition per photo =32 frame runs. Each repeat block contains all four conditions on both photos before the next block; Latin-square rotation of condition order and alternating first photo. If budget stops early, report incomplete cells and analyze equal completed repeat blocks separately. Do not count a partial block as a balanced result.
- Final acceptance unchanged: local presence>=.80; local single>=.90; Box confidence>=.60 OR cross-crop IoU>=.75; no artificial crop/padding truncation; unmasked fraction>=.50; duplicate IoU .85 unresolved. Mask is accepted model predictions +4px. No confidence promotion.
- Bounding boxes: visible printed-card AABB, exclude sleeve/hidden parts; clip only at original image edges. Do not infer hidden whole-card geometry. Target choice stays leftmost at search / nearest center in original crop. Known card count/names/positions are evaluation-only, never inference hints.
- Save every condition/repeat/photo prediction before opening scoring truth. Primary metric adopted TP/FP/FN at IoU .50/.75; report per-run raw search Boxes and raw crop Boxes separately, unique matched truth count, duplicate predictions, geometric duplication, timing, actual API/usage/cost and stop causes. Raw stages have different proposal counts because the unchanged algorithm is adaptive; do not present them as an exhaustive detector.
- Report mean/range/paired variation, not a single lucky run. These photos mix physical factors, so do not claim separate causal effects of actual rotation, overlap or object occlusion. Identification/OCR is outside this wording comparison.
- Anonymous aggregate/code/protocol in a new stacked PR, no merge. Images/crops/truth coordinates/raw API responses/private Library identity stay private. User-only image report<=5MB, save to Library.

## Exact four prompt profiles
Generic target is exactly “trading card”; “card used in a card game” is not tested as an additional synonym. Every profile contains the actual five instructions used by multi; only target replacement and the same appended eligibility sentence differ.

### mtg_plain
```json
{
  "search_present": "Is any Magic: The Gathering card printing visible outside the gray masked areas in photo?",
  "search_box": "The visible printed area of the leftmost Magic: The Gathering card outside the gray masked areas in photo.",
  "crop_present": "Is a Magic: The Gathering card visibly present in this crop?",
  "crop_single": "Does this crop primarily show one identifiable Magic: The Gathering card? Ignore tiny neighboring edge fragments.",
  "crop_box": "The visible printed rectangle of the Magic: The Gathering card nearest the center of this crop, excluding sleeve and hidden parts."
}
```

### mtg_explicit
```json
{
  "search_present": "Is any Magic: The Gathering card printing visible outside the gray masked areas in photo? Treat a card as eligible even if it overlaps another card, is rotated, or is partly occluded by an object placed on it.",
  "search_box": "The visible printed area of the leftmost Magic: The Gathering card outside the gray masked areas in photo. Treat a card as eligible even if it overlaps another card, is rotated, or is partly occluded by an object placed on it.",
  "crop_present": "Is a Magic: The Gathering card visibly present in this crop? Treat a card as eligible even if it overlaps another card, is rotated, or is partly occluded by an object placed on it.",
  "crop_single": "Does this crop primarily show one identifiable Magic: The Gathering card? Ignore tiny neighboring edge fragments. Treat a card as eligible even if it overlaps another card, is rotated, or is partly occluded by an object placed on it.",
  "crop_box": "The visible printed rectangle of the Magic: The Gathering card nearest the center of this crop, excluding sleeve and hidden parts. Treat a card as eligible even if it overlaps another card, is rotated, or is partly occluded by an object placed on it."
}
```

### generic_plain
```json
{
  "search_present": "Is any trading card printing visible outside the gray masked areas in photo?",
  "search_box": "The visible printed area of the leftmost trading card outside the gray masked areas in photo.",
  "crop_present": "Is a trading card visibly present in this crop?",
  "crop_single": "Does this crop primarily show one identifiable trading card? Ignore tiny neighboring edge fragments.",
  "crop_box": "The visible printed rectangle of the trading card nearest the center of this crop, excluding sleeve and hidden parts."
}
```

### generic_explicit
```json
{
  "search_present": "Is any trading card printing visible outside the gray masked areas in photo? Treat a card as eligible even if it overlaps another card, is rotated, or is partly occluded by an object placed on it.",
  "search_box": "The visible printed area of the leftmost trading card outside the gray masked areas in photo. Treat a card as eligible even if it overlaps another card, is rotated, or is partly occluded by an object placed on it.",
  "crop_present": "Is a trading card visibly present in this crop? Treat a card as eligible even if it overlaps another card, is rotated, or is partly occluded by an object placed on it.",
  "crop_single": "Does this crop primarily show one identifiable trading card? Ignore tiny neighboring edge fragments. Treat a card as eligible even if it overlaps another card, is rotated, or is partly occluded by an object placed on it.",
  "crop_box": "The visible printed rectangle of the trading card nearest the center of this crop, excluding sleeve and hidden parts. Treat a card as eligible even if it overlaps another card, is rotated, or is partly occluded by an object placed on it."
}
```

## Acceptance
Preserve all four outcomes even if worse, separate single-run from repeat-stable differences, retain recognition failures distinctly from connection/usage blockers, and leave 8-card goal unmet unless measurements actually meet it.


## Pre-scoring continuation note

After the first complete eight-run block, the runner stopped on a known-usage malformed model Box (zero area) in mtg_explicit / scene-B. Offline replay reproduced the same geometry error, with no API calls. No truth was read. This is a model-answer failure, not a connection/free-balance/usage failure. Keep that partial frame and its error in the comparison; do not rerun it or exclude it. The remaining pre-planned runs may continue only after verifying the exact saved prefix and durable known usage. Deduct all prior calls/cost from the original 384 HTTP / US$0.05 phase limits. The core detector, prompts and final gates stay fixed. Provider errors, unknown usage, pending or budget/call limits still stop the phase. See the pre-continuation record in Issue #23.
