# Phase 7 live validation plan

## Completed without Telegram delivery

- The manual `phase7-probe.yml` workflow reads all 15 configured AI feeds, calls the configured Gemini model for one real RSS article and one batch of up to four pending articles, validates structured output, and checks the state file hash. It has `contents: read`, only `GEMINI_API_KEY`, and no Telegram credentials. Run 42189873207 on `main` completed successfully with 15 feeds, zero feed errors, 263 recent posts, a four-article batch, and unchanged state. The single-item call recovered from one classified `SERVER_ERROR` on attempt 2. Two batch items used the original source headline after editorial grounding rejection. Run 42189317440 had already confirmed the single-item call before the batch probe was added.
- Offline controlled fixtures cover the seven-day ceiling, three older articles per run, retry after Gemini failure, partial Telegram failure with Git state persistence, and the documented feed-eviction gap.

## Bounded production delivery procedure (executed once)

1. Before dispatch, confirm `main` still has `gold_price.enabled: false`, `features.audio_enabled: false`, and AI `message_thread_id: 5`; check current state for an unresolved `uncertain` AI item and stop if present.
2. Dispatch **once**: `gh workflow run agent.yml --repo Smart-create001/Agent_repost-news --ref main -f mode=alert -f phase6_test=true -f phase6_topic=ai_tech`.
3. `scripts.phase6_test --text-only` limits selection to one AI article and `BoundedNotifier` permits at most one single-chunk `[TEST]` Telegram text request for thread 5. It cannot enable the disabled gold topic or audio. A relevant article is required for an actual send; no message is also a valid no-op outcome.
4. Inspect the workflow logs for an explicit Telegram message ID and thread 5, the state commit/push result, and the saved `seen_posts.json`. Run `verify_state` afterward and read the committed delivery record back. Do not reset state or force push. If delivery becomes `uncertain`, stop and reconcile manually rather than retrying.

The user authorized one production attempt, which has now been consumed. A naturally occurring missed schedule is still needed to confirm the catch-up checkpoint in production; the controlled fixtures are not a substitute for that observation.

## One authorized delivery attempt on the current production main

Run 42192412882 sent one AI text message to configured thread 5. Telegram returned message ID 171, persisted by state commit `4deea3b`; read-only run 42192604876 loaded the same state hash (`0f785ce5dca61d8a9ea2ba6d1b5ea1dd0467e7f566ac32163fdf1fde94f9b71d`). Gold and audio were disabled. The production test mode used the older prefix `[TEST — ทดสอบ Phase 6]`, so it did not satisfy the requested exact prefix `[TEST]`. The prefix is corrected on the separate pending-queue branch; the one-message live allowance has been consumed and no second send is planned.
