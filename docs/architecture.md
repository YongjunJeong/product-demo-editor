# Local execution architecture and decisions

## Local preprocessing

FFprobe selects the first non-cover-art video stream and first audio stream. The timeline
origin is the selected video's first timestamp. Audio is normalized to that origin with
its relative offset preserved, then converted to 16 kHz mono for both silence and ASR.
When a container lacks stream duration, a streaming packet scan finds the final timestamp;
container duration alone can include a nonzero initial timestamp.

FFmpeg downsizes frames for pixel-change detection. This is not visual semantic analysis.
Faster-whisper runs on CPU/int8 with Korean language, VAD, beam size 5 and word timestamps.
The small model is a starting point, not a claim of optimal Korean quality or Mac speed.

## Data over opaque calls

Pydantic validates configuration, intervals and transcript structure. JSON artifacts use
source-relative seconds, half-open intervals and a schema version. Missing audio and
user-disabled ASR produce explicit skipped states, not fabricated transcripts.

The pipeline writes atomic checkpoints and a source/config/version manifest. Resume checks
artifact hashes. Models and ASR are optional for deterministic analysis. A missing ASR model
is an actionable error. No fallback to remote inference is allowed.

## Boundaries deferred deliberately

M4 compiles approved candidates into a non-overlapping output timeline, remaps subtitle
times, and rejects malformed or conflicting decisions.
CapCut remains an optional export adapter. Agent-hosted translation and whole-segment semantic edit proposals are described in
[agent-workflow.md](agent-workflow.md). Visual semantic analysis remains unimplemented.

## Metrics

RTF = wall processing seconds / source video seconds. It includes hashing, inspection,
ASR model loading and preprocessing, but excludes separate package/model installation.
Stage times include necessary audio extraction in the stage that first requests audio.
Repeated runs record cached stages and preserve prior metric files. External inference
cost is zero in M1 by architecture, not a measurement of electricity or developer usage.

## M2 manual translation exchange

Text-only correction files are bound to the original transcript hash. Segment IDs and
boundaries are preserved; corrected wording clears obsolete ASR word alignment. Translation
requests embed a compact corrected transcript and glossary, not raw media or model paths.
Responses must match the immutable request hash and exact source ID set.

Import validates the full exchange before creating a new output directory. It retains exact
input bytes for provenance. SRT times are integer milliseconds on the uncut source timeline.
Two-line cues partition each source segment, with proportional timing for split translations.
Reading-speed and glossary diagnostics are review suggestions, never proof of semantic
accuracy. M2 outputs remain draft; M3 adds explicit approval and preview rendering.


## M3–M4 execution and review

Draft plans contain source hashes, subtitle cues, protected speech and unselected silence
candidates. Approval explicitly selects cut IDs and rejects the rest. The approval digest
covers both the exact plan and compiled timeline; execution verifies both before rendering.
This is tamper detection, not user authentication. Draft previews do not create approval.

The compiler uses a configured CFR grid (25/30/60 fps) to avoid cumulative per-cut rounding
drift. FFmpeg normalizes the input, trims video by frame indices and audio by 48kHz sample
indices, then concatenates retained spans. The original audio/video stream offset is
preserved. Caption times are translated through the same retained-span mapping. Partial
final frames are excluded and reported. Legacy plans reject speech/caption-overlapping cuts. Schema 1.2 permits only an explicit
whole-segment exception for a declared semantic cut; retained speech/captions stay guarded.

The clean video and burned-in preview are separate stages with verified hash checkpoints.
Partial output is renamed only after duration validation. Failed runs retain completed
stages and logs. Resume rejects changed plans or tools and regenerates corrupted derived
videos. Renderer selection checks libass support, including Homebrew's keg-only ffmpeg-full.


## M5 speed operations

Schema 1.1 adds user-selected speed ranges (greater than 1x, at most 4x) and per-range audio
policy. Speech/caption overlap and simultaneously accepted cut/speed overlap are rejected.
No speed is selected implicitly. Existing v1.0 approved payloads and compiled layouts stay
compatible; changing an approved operation still invalidates its approval hash.

The compiler splits retained source spans at speed boundaries and rounds each requested
output length to a positive integer frame count. Requested and effective rates are both
recorded. FFmpeg retimes the corresponding video and uses chained atempo filters (each at
most 2x) for pitch-preserving audio; mute is the default on sped-up waits. Audio is padded/
trimmed to the exact compiled sample count. The renderer checks both audio and video
output durations. Unaffected captions shift by cumulative cut and speed savings.


## M6 local file exchange

The optional CapCut adapter consumes a validated approval and completed render. It checks
plan/timeline equality, approval binding, both video hashes and exact output-time SRT.
It copies media without re-encoding into a new directory, then writes reconstruction
metadata and a final completion manifest. Bundle verification rejects unsafe paths,
symlinks, missing files, changed hashes and mismatched approval/timeline/subtitles.

Installation detection reads macOS app metadata only. It does not assert compatibility,
launch CapCut or access its project internals. Manual UI testing is recorded separately.
The runtime adds no network calls or dependencies. Captions stay editable after SRT
import; cuts and speeds are baked into the clean video. Reference marker JSON is not a
CapCut import format. Optional original-media copies do not rewrite the approved plan.


## M7 loopback review UI

A standard-library threaded HTTP server binds only 127.0.0.1. Static HTML/CSS/JS has no
external assets or framework dependency. A single background worker invokes the existing
analysis, translation-exchange, planning, rendering and CapCut adapter functions. HTTP
state polling stays responsive while media jobs run; concurrent mutations are rejected.

Each successful plan operation creates a new immutable file. A preview records its plan
hash and review ID; approval checks both before issuing the existing approval envelope.
Starting a new draft operation invalidates active approval, even if that operation fails.
The UI disables approval on unsaved edits and locks input during jobs. Artifact files
survive restart; the active UI session does not. Reopening a plan requires fresh review.

Per-process random tokens protect state/mutation/media routes; Host and Origin checks
reject cross-origin access. Only registered media IDs can be streamed, with range support.
No arbitrary-path HTTP file server, shell command endpoint or upload route is exposed.
The development server is for a trusted single local user, not public hosting.


## M8 session persistence and recovery

Atomic JSON checkpoints store each project's active state, retry arguments and media IDs
under sessions/. A separate pointer selects the last project. Session access tokens are
never persisted. Startup validates source/plan hashes, preview integrity, approvals and
completed outputs; changed or missing files invalidate the recovered approval. Interrupted
jobs become idle with an explicit retry action. Retry uses the same inputs and new output
folders, without implicitly reusing incomplete caches.

A local authenticated directory browser selects video paths without uploading or copying
media. Draft saving preserves text/translation inputs and cut/speed selections separately
from approved artifacts. Analysis/render callbacks expose stage labels, and the UI shows
elapsed wall time without claiming a percentage or ETA. Only one server per work directory
and one editing tab are supported.

## Agent skill transition

The host agent reads compact text context and writes a bound response with complete English
translations and explained proposals. The runtime makes no provider calls. Host inference
may be remote and its usage is not measured by local tools. Agent responses are drafts;
structural validation cannot certify editorial correctness. See [the exchange contract](../skills/product-demo-editor/references/exchange.md).
