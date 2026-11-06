# Phase 7 pending queue: deployment and rollback plan

This branch is a proposal. The production `main` workflow and state have not been changed by this branch. Separate deployment approval is required.

## State migration

The existing `schema_version: 1`, `posts`, `briefings`, delivery IDs, and alert checkpoint remain unchanged. Each enabled topic may gain `pending_posts` and `pending_metrics`. Missing fields mean an empty queue and zero counters. Each pending entry has its canonical URL, bounded title (500 characters), bounded description (3,000 characters), publication time, feed URL, discovery time, and inferred-date flag. Invalid entries stop state loading; the corrupted file is never silently overwritten. A first alert capture writes the additive fields and pushes the state before any Gemini or Telegram call.

The queue capacity is 300 entries per topic. When full, additional RSS candidates are counted as overflow attempts, `overflow_unresolved` remains true, the checkpoint cannot advance, and the workflow reports failure until an operator reviews the gap. They are not claimed to be durable. The capture runs again on the next schedule, but an overflowed item may disappear from the feed first. The queue does not recover articles removed before the bot ever discovered them.

A read-only check on 6 November 2026 found 330 distinct AI candidates in the 15 feeds within seven days, with no feed errors. The production state contained only 28 delivered AI records at that time. The first deployment capture could therefore exceed capacity 300; do not deploy without reviewing capacity or an explicit bootstrap policy. This count is a point-in-time observation, not a guarantee about the next run.

## Deployment gate

1. Obtain separate approval to merge the branch. Recheck `main`, branch diff, workflow YAML, 15 feeds, model, thread 5, and disabled gold/audio settings.
2. Back up the current Git-tracked state without changing it. Confirm there are no unresolved `uncertain` AI deliveries.
3. Merge without force push. On the first alert run, inspect `pending_capture`, `overflow`, and `invalid` metrics and the state commit pushed by the capture step.
4. Verify the remote `main` commit and its `seen_posts.json` bytes before processing. The process rejects a missing/mismatched commit. Before each Telegram request, it pushes an `uncertain` intent; after a confirmed result, it pushes the result. A failed push stops processing. The workflow's final state push handles checkpoint and irrelevant/expiry updates.
5. Read the committed queue and delivery record in the next workflow run. Do not call the queue durable based only on a local file or an artifact.

## Rollback

Do not reset `seen_posts.json` or discard `pending_posts`. If the new workflow fails, stop further alert dispatch while preserving the current state and reconciliation artifact. Inspect the last confirmed remote commit and any `uncertain` items. Revert code/workflow only after the queue is drained or a reviewed migration retains and processes its entries. Re-enable dispatch only after a follow-up run reads the preserved state. No force push or blind state merge is part of rollback.

## Current verification boundary

Offline tests use a local bare Git remote to exercise capture push, remote readback, conflict rejection, runner interruption, replay, expiry, overflow, and quarantine. They do not prove GitHub token permission or production persistence. The authorized bounded live Telegram test uses the current production workflow; it does not test this undeployed queue.
