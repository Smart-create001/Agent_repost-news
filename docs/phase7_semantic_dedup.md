# Phase 7 semantic duplicate detection: offline design

The production bot currently deduplicates canonical URLs and exact normalized headlines within one fetch. It does not suppress paraphrased coverage across publishers or later runs. Keeping distinct coverage is safer than hiding a material update.

The offline prototype in `tests/semantic_dedup_prototype.py` uses title tokens, a shared product/version signature, a seven-day event horizon, and material-change cues. The controlled cases cover the same URL, a paraphrased release, an updated capability, a security story about the same product, and an unrelated model. It uses only local Python code and no embedding service or paid API. Its labels are candidate decisions, not verified facts.

Before production use, retain a topic-scoped `event_id` and revision history in the existing state store. Compare each candidate with previously delivered revisions, require source-grounded material differences, and keep an update if new capabilities, availability, dates, prices, limits, or corrections appear. Uncertain pairs should remain distinct. Review a labeled sample of real RSS titles and measure false merges before enabling suppression. Changing the live deduplication policy and state format requires approval; this prototype is not imported by `src/`.
