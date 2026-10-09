# Agent exchange

`request.json` contains source-relative segment times/text, glossary, target duration and
verified waiting candidates. It omits raw media, local source paths, ASR model paths and
word arrays. Sibling `base-plan.json` retains local execution details; it is not model
context. The exchange itself makes no model/API calls. The current host agent reads it.

Copy `request_id` and `request_sha256` from `response.template.json` unchanged. Fill
`segments: [{id, text}]` with one nonempty English translation per source ID. `engine`
identifies the actual host (for example `Codex`); do not invent a model name or token bill.
`uncertainties` lists ASR, terminology, context and visual-review issues.

Each proposal has a unique `id`, `rationale`, `confidence` (`low|medium|high`) and
`requires_visual_review` boolean. Exactly one of these shapes applies:

```json
{"id": 1, "kind": "cut_wait", "candidate_id": 3,
 "rationale": "Long gap between explanations; confirm screen activity",
 "confidence": "medium", "requires_visual_review": true}
```

```json
{"id": 2, "kind": "speed_wait", "candidate_id": 4, "rate": 3,
 "rationale": "Compress a wait while retaining visible progress",
 "confidence": "medium", "requires_visual_review": true}
```

```json
{"id": 3, "kind": "remove_speech", "segment_ids": [8, 9],
 "rationale": "Abandoned take, followed by a complete restatement",
 "confidence": "medium", "requires_visual_review": true}
```

Use actual exported IDs; examples are illustrative. Do not combine cut and speed on
the same candidate. Speech IDs must be consecutive in transcript order. Whole speech
removal includes 150ms guard margins quantized outward; touching retained speech/captions
is rejected. If an isolated removal is unsafe, retain it rather than expanding into other
speech. Partial-sentence edits and automatic visual loading detection are unsupported.

The importer binds requests/responses/artifacts to hashes, validates IDs and conflicts,
generates captions through the existing subtitle compiler, and emits schema 1.2 plans.
The compiler drops captions wholly belonging to selected speech removals and remaps
retained captions to output time. Rejecting a proposal retains its speech and captions.
Existing 1.0/1.1 approvals remain compatible. Inferred semantic correctness is not verified
by structural validation; review is still necessary.

```sh
video-agent agent-export runs/<id> --goal 'Keep the necessary demo steps' --output work/exchange
# Host agent writes work/exchange/response.json.
video-agent agent-import work/exchange/request.json work/exchange/response.json --output work/draft
video-agent preview work/draft/plan.json --cuts 1 --speeds 1 --output work/preview
# Only after reviewing and approving those exact edits:
video-agent approve work/draft/plan.json --cuts 1 --speeds 1 --output work/approved.json
video-agent render work/approved.json --output work/render
video-agent export-capcut work/approved.json work/render --output work/capcut
video-agent verify-bundle work/capcut
```

Use fresh paths and actual operation IDs from `agent-report.json`; empty selection means
no edits. Model inference may occur on the host provider; `local_tool_external_requests`
does not measure that host inference. No nested CLI, API key, or provider SDK is required.
