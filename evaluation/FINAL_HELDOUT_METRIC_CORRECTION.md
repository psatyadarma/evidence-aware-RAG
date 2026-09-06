# Final Held-out Metric Correction Audit

Discovered and corrected at: `2026-09-05T08:27:05.828419+00:00`.

## Status and scope

This is a post-hoc **evaluation-only** correction to derived Checkpoint 12 metrics. The original one-shot inference run, raw reports, summaries, comparison, model outputs, gates, systems, prompts, threshold, corpus, and benchmark remain unchanged.

## Exact bug

The original System C evaluator set `substantive_answer` from non-empty response text. Deterministic abstentions intentionally contain explanatory refusal text, so all 28 refusals were incorrectly treated as substantive answers. Correct semantics use explicit execution/action fields: a generated answer requires `generation_called=true` with no abstention or provider failure; abstention uses `abstained=true`. String length is never a routing signal.

## Affected metrics

System C generated-answer count/coverage, correctness denominator/rate, citation denominator/rate, required-evidence denominator/rate, unsupported-answer count/rate, and error categorization were affected. Corrected System C values are: generated `17/45`, unsupported `1/20`, computation correctness `14/17`, citations `17/17`, and required evidence `13/17`.

## Metrics not affected

Raw generation calls, deterministic abstention count/rate, correct-abstention count, abstention precision/recall, correct-computation count, partial-success count, qualification-retention count, provider failures, calls, token usage, cost, generation latency, and the narrow machine-checkable end-to-end success count were already derived from explicit fields and remain unchanged. System A and B sanity checks reproduce their original summaries.

## No new inference or provider activity

The correction command reads existing JSON/JSONL files and uses Python standard-library arithmetic plus `evaluation/posthoc_metrics.py`. It does not import application pipelines, provider adapters, HTTP clients, or model SDKs; it does not access API credentials. No held-out question was rerun and no new provider attempt was created. Tests enforce this import boundary.

## Genuine errors retained

- System C actual unsupported answer: `t029`.
- System C unnecessary abstentions: `t001, t003, t005, t013, t017, t021, t022, t024, t025`.
- Corrected System C error counts: `{"answered_unsupported_causal_request": 1, "computation_error": 3, "failed_partial_answer": 6, "unnecessary_abstention": 9}`.
- Correct deterministic refusals are no longer labelled as causal, temporal, unavailable-value, false-premise, or out-of-domain hallucinations.

## Development-to-held-out observations

- system_a: generated_answer_coverage 0.4400 → 0.2667 (Δ -0.1733), correctness_among_generated 0.5455 → 0.7500 (Δ +0.2045), abstention_recall 1.0000 → 1.0000 (Δ +0.0000), unsupported_answer_rate 0.0000 → 0.0000 (Δ +0.0000), partial_success_rate 0.3333 → 0.1111 (Δ -0.2222), end_to_end_success 0.7200 → 0.6444 (Δ -0.0756)
- system_b: generated_answer_coverage 0.4800 → 0.2667 (Δ -0.2133), correctness_among_generated 0.5000 → 0.6667 (Δ +0.1667), abstention_recall 1.0000 → 0.9500 (Δ -0.0500), unsupported_answer_rate 0.0000 → 0.0500 (Δ +0.0500), partial_success_rate 0.3333 → 0.1111 (Δ -0.2222), end_to_end_success 0.7200 → 0.6000 (Δ -0.1200)
- system_c: generated_answer_coverage 0.5200 → 0.3778 (Δ -0.1422), correctness_among_generated 1.0000 → 0.8235 (Δ -0.1765), abstention_recall 1.0000 → 0.9500 (Δ -0.0500), unsupported_answer_rate 0.0000 → 0.0500 (Δ +0.0500), partial_success_rate 1.0000 → 0.3333 (Δ -0.6667), end_to_end_success 1.0000 → 0.7333 (Δ -0.2667)

These are descriptive gaps only. No system, prompt, threshold, parser, retrieval rule, benchmark item, or prediction was changed in response.

## Original artifact SHA-256 values

- `final_heldout_run_started.json`: `d7a7fccabb84283f58bd9b9e1c2aa1a4a8d6556d8aaa658d058c1362650a0cf1`
- `final_heldout_system_a_report.jsonl`: `e789c1d697382569d89c15af6c775dec5be27c12df26cc083cf8a834aac9626d`
- `final_heldout_system_a_summary.json`: `69bd27bc35b6575e9b905dcdd9cd73d417bf5dbb41eb9482939a7da3da531b5c`
- `final_heldout_system_b_report.jsonl`: `0045bd996c94cb2852a7a929d4ba62c4f76ee50b5d94647940a62f98dba730ff`
- `final_heldout_system_b_summary.json`: `46af121a52086583df805b324b94a20207cf291850f0db0d85d09a948eeb7f02`
- `final_heldout_system_c_report.jsonl`: `8b4790425e9253de8215172e56ca73ace4ed1da06a31f29f625086993e088461`
- `final_heldout_system_c_summary.json`: `f30d405b492c4ba9e22f03f64821676a675093afbcf44994014052748a7a3a3e`
- `final_heldout_comparison.json`: `d9877709f42ac6f3398676d9d7b3864be0bd9b6fde08da9d74d7323cd563c96c`
- `final_heldout_run_completed.json`: `6eabe981b7de6dab49635ab4998a5d1fca4168b6719d5a93c9a48e40beed241c`

The correction script verifies these values both before and after writing derived artifacts.

## Corrected derived artifact SHA-256 values

- `evaluation/results/final_heldout_system_a_metrics_corrected_v1.json`: `47d17df7e4856caf4eb2e0c62942bac3cb607a70decabda6d224529fb0a9bc4f`
- `evaluation/results/final_heldout_system_b_metrics_corrected_v1.json`: `b7d4388022ce0c2e69d7812cb8af4d3e1a61715c510ca7b227935e372db9aef1`
- `evaluation/results/final_heldout_system_c_metrics_corrected_v1.json`: `5b2842f988a879bb3d739c0afb2574112f677163260f1d7da2149f7f2f7a9e3d`
- `evaluation/results/final_heldout_comparison_corrected_v1.json`: `570809100027ce0693af4a95395ccf2cc58af7618ee8102c9fcc093722490b3f`
