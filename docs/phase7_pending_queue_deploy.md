# Phase 7 pending queue: deployment and rollback plan

This branch is a proposal. The production `main` workflow and state have not been changed by this branch. Separate deployment approval is required.

## State migration

The existing `schema_version: 1`, `posts`, `briefings`, delivery IDs, and alert checkpoint remain unchanged. Each enabled topic may gain `pending_posts` and `pending_metrics`. Missing fields mean an empty queue and zero counters. Each pending entry has its canonical URL, bounded title (500 characters), bounded description (3,000 characters), publication time, feed URL, discovery time, and inferred-date flag. Invalid entries stop state loading; the corrupted file is never silently overwritten. A first alert capture writes the additive fields and pushes the state before any Gemini or Telegram call.

The proposed queue capacity is 600 entries per topic. The 300 and 500 limits remain covered by controlled tests. When full, additional RSS candidates are counted as overflow attempts, `overflow_unresolved` remains true, the checkpoint cannot advance, and the workflow reports failure until an operator reviews the gap. They are not claimed to be durable. The capture runs again on the next schedule, but an overflowed item may disappear from the feed first. The queue does not recover articles removed before the bot ever discovered them.

At 2026-11-06 09:21:56 UTC, a read-only check against the latest `main` state and the 15 live feeds found 330 distinct candidates within seven days and zero feed errors. Of those, 19 were already delivered, zero processed, zero pending, and 311 truly new. The state had 28 delivered AI records overall. The JSON state was about 170 KB before adding the queue.

| Capacity | Initial queue | Overflow | Estimated JSON | Optimistic drain, no new arrivals | Expiry during drain |
|---:|---:|---:|---:|---:|---:|
| 300 | 300 | 11 | 468 KB | 31 scheduled runs, about 60 hours | 2 |
| 500 | 311 | 0 | 478 KB | 32 scheduled runs, about 62 hours | 2 |
| 600 | 311 | 0 | 478 KB | 32 scheduled runs, about 62 hours | 2 |

The drain simulation uses the current three older posts per run, 20 total posts per run, and seven-day expiry, assumes every selected item finishes successfully, and ignores new arrivals. It is an optimistic estimate, not a delivery forecast. Capacity 600 leaves 289 slots after bootstrap and more room for arrivals while draining; capacity 500 leaves 189. Two items would still expire under this snapshot and policy even without new arrivals. Expiry is counted and reported, never silently used to make overflow disappear. A later feed snapshot may differ.

## State reconciliation before deployment

The branch merge base is `88ff08a`. Its `seen_posts.json` bytes are identical to the branch's; the branch contains no state-file change. The later production `main` commit `4deea3b` has a different state blob, SHA-256 `0f785ce5dca61d8a9ea2ba6d1b5ea1dd0467e7f566ac32163fdf1fde94f9b71d`, containing 28 confirmed AI deliveries including Telegram message ID 171 and no AI `uncertain` delivery. Keep this newer `main` blob when merging. Never copy the branch's older state file over it. Recheck `main` immediately before any approved merge because Actions may advance it again.

## Deployment gate

1. Obtain separate approval to merge the branch. Before merge, set repository variable `PENDING_QUEUE_CAPTURE_ONLY=true`. The workflow will capture and persist during scheduled alert and briefing runs while skipping Gemini and Telegram processing. Manual `capture_only` does the same.
2. Recheck `main`, branch diff, workflow YAML, 15 feeds, model, thread 5, and disabled gold/audio settings. Back up the current Git-tracked state from the latest `main` commit without changing it. Confirm no unresolved `uncertain` AI deliveries and preserve message ID 171.
3. Merge without force push. Keep `PENDING_QUEUE_CAPTURE_ONLY=true` for the entire capture validation period. On capture error, processing is skipped; a valid partial queue may be pushed for reconciliation and the workflow remains failed.
4. Gate 2 verifies the remote commit and state bytes in the capture runner, then a separate fresh runner checks out `main`, validates the state, reports queue/overflow/expiry metrics, and prints the state hash. The capture-only verification job has read-only permissions and does not receive Telegram secrets or Gemini credentials.
5. After Gate 2 passes, request separate approval for controlled processing. Only then set `PENDING_QUEUE_CAPTURE_ONLY=false` and dispatch the explicitly approved bounded processing run. Before each Telegram request, it pushes an `uncertain` intent; after a confirmed result, it pushes the result. A failed push or mismatched remote commit stops processing. Never blind retry `uncertain` items.
6. Read the committed queue and delivery record in a subsequent workflow run. Do not call the queue durable based only on a local file or artifact.

## Rollback

Do not reset `seen_posts.json` or discard `pending_posts`. If the new workflow fails, stop further alert dispatch while preserving the current state and reconciliation artifact. Inspect the last confirmed remote commit and any `uncertain` items. Revert code/workflow only after the queue is drained or a reviewed migration retains and processes its entries. Re-enable dispatch only after a follow-up run reads the preserved state. No force push or blind state merge is part of rollback.

## Current verification boundary

Offline tests use a local bare Git remote to exercise capture push, remote readback, conflict rejection, runner interruption, replay, expiry, overflow, and quarantine. They do not prove GitHub token permission or production persistence. The authorized bounded live Telegram test uses the current production workflow; it does not test this undeployed queue.

Capture-only validation has been added to the branch but has not been run on GitHub Actions. Gate 1 backup and merge are not performed; Gate 2 has not been run; Gate 3 has not been run. Do not treat the previous single-message Telegram test or offline regression as proof of durable queue deployment.
