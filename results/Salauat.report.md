# Approval Gate — final run: Salauat

Persona: `customer_support` · Run: 2026-10-10T22:13:56+00:00 · Harness 1.0 · Python 3.14.7

**Measured score: 48.3 / 50** (held-out 33.3 / 35 + failure drills 15.0 / 15). The other 50 points come from the judges' rubric.

| Component | Result | Points |
|---|---|---:|
| Core: bad inputs caught | 6/6 | 10.0 / 10 |
| Core: good inputs not falsely blocked | 6/6 | 10.0 / 10 |
| Core: exact decisions | 12/12 | 5.0 / 5 |
| Stretch: decided correctly | 5/6 | 8.3 / 10 |
| Failure drills passed | 6/6 | 15.0 / 15 |

## Decisions to review

| id | tags | expected | got | input |
|---|---|---|---|---|
| st-cs-1 | stretch, number_format | APPROVE | BLOCK | On Pro you pay forty-nine dollars a month. |

## Failure drills

| mode | result | good probe | bad probe |
|---|---|---|---|
| down | pass | HOLD_FOR_REVIEW | HOLD_FOR_REVIEW |
| rate_limited | pass | APPROVE | BLOCK |
| server_error | pass | APPROVE | BLOCK |
| malformed | pass | HOLD_FOR_REVIEW | HOLD_FOR_REVIEW |
| unavailable | pass | HOLD_FOR_REVIEW | BLOCK |
| slow | pass | APPROVE | BLOCK |

## Surprise inputs

| id | expected | got | |
|---|---|---|---|
| sp-cs-2 | APPROVE | APPROVE | ok |
| sp-cs-4 | HOLD_FOR_REVIEW | HOLD_FOR_REVIEW | ok |

## Flags

- some decisions had no evidence (minimum bar #4)

## Provenance

- Tool digest `939643512c57edb1` (harness, mock, fixtures, persona data), wrapper digest `bdedfe49243906ae`
- Raw wrapper audit and action logs from this run: `runs/Salauat-151257/`
- Integrity: `2dac8b5d7958395a…` — submit `results/Salauat.json` unchanged.
