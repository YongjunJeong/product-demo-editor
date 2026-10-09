# M1 measured validation

Measured locally on Apple Silicon, Python 3.12.13, FFmpeg 9.0.2,
faster-whisper 1.2.1 / CTranslate2 4.8.2. CPU int8, 4 threads, model small.
Model snapshot: `~/.cache/video-agent/models/models--Systran--faster-whisper-small/snapshots/536b0662742c02347bc0e980a01041f333bce120`.

## Korean synthetic speech

Source: `work/korean-demo.mp4` (1280×720, 30 fps, macOS Yuna speech).
Reference: 안녕하세요. 오늘은 새로운 고객 세그먼트를 만드는 방법을 보여 드리겠습니다. 고객 메뉴에서 조건을 추가하고 저장 버튼을 누르면 됩니다.

- Duration: 9.40 seconds
- Analysis wall time: 3.243 seconds
- Local ASR stage including model load: 2.987 seconds
- RTF: 0.345
- Speech segments: 2
- Remote semantic calls / media uploads: 0
- Verified resume time: 0.082 seconds, all 4 stages reused, no ASR calls
- Run: `20260929T150031Z_e11c60c9` (original metrics preserved as `metrics_previous_*.json`)

Recognized text:

- 0.00–4.76: 안녕하세요 오늘은 새로운 고객 세그먼트를 만드는 방법을 보여드리겠습니다.
- 5.24–8.82: 고객 메뉴에서 조건을 추가하고 저장 버튼을 누르면 됩니다.

This is one synthetic sample and confirms local end-to-end operation, not real-world
Korean transcription accuracy. No WER or editing time saving is claimed. Downloads and
installation are excluded. Commands were run with `HF_HUB_OFFLINE=1`.

## Automated checks

9 tests passed. Tests use real FFmpeg-generated media and cover:

- 10-second clip, exact known cut at 5 seconds and silence at 0–2 / 8–10 seconds
- Unicode paths and preservation of source SHA-256
- Cached resume, corrupt checkpoint regeneration and incompatible config rejection
- No audio track and entirely silent audio
- Simulated ASR failure followed by resume
- Missing input and missing model with friendly CLI errors
- Invalid configuration / interval rejection
- Variable frame rate with a nonzero timeline origin
- Delayed audio retaining its offset relative to video

Ruff lint and formatting checks passed. Real ASR smoke test is separate from unit tests.

## Not yet verified

Human screen-recorded speech, accents, product terminology, long 4K recordings,
multiple audio track selection, subtitle output and CapCut import.

## M2 manual subtitle exchange (2026-09-30)

Used the existing synthetic Korean transcript from M1. Exported a correction template,
added punctuation to the first segment, exported a translation request with the glossary,
filled two English example translations locally, and imported them through the real CLI.

- Output: `work/m2-demo/subtitles/subtitles.en.srt`
- Cues: 2; source ranges 0.00–4.76s and 5.24–8.82s
- Automatic formatting/glossary warnings: 0 (not a semantic quality score)
- Three CLI operations plus local fixture preparation: 0.417s wall time
- ASR reruns, media uploads and external translation calls: 0
- Manual translation tokens/cost: unknown, recorded as null
- Full suite: 23 tests passed; Ruff lint and formatting passed

M2 tests cover correction provenance, cleared stale word alignment, exact ID coverage,
blank or stale responses, overwrite prevention, Unicode paths, source-time cue splitting,
reading-speed/glossary warnings, reordered responses, long identifiers, empty transcripts
and exact-byte provenance copies. The generated request omits word alignment and local
model paths. English examples were supplied manually; no translation engine was evaluated.


## M3–M4 rendering and selected cuts (2026-09-30)

- Korean synthetic demo: 9.4s → 9.4s; clean video plus burned-in
  English preview rendered in 1.307s. No cuts selected.
- Known-cut fixture: 10.0s → 6.8s; removed 0.2–1.8s and 8.2–9.8s,
  80 frames at 25fps. Render wall time 0.682s.
- Original scene change at 5s moved to 3.4s. Source caption 3.0–4.5s moved to 1.4–2.9s.
- Generated preview frames were visually inspected: captions visible, inside the frame,
  and limited to two lines. Synthetic content cannot validate avoidance of real UI controls.
- Final suite: 33 tests passed in 11.81s. Ruff lint/format checks passed.

Rendering tests inspect real output video/audio durations, actual scene pixels, presence of
burned-in caption pixels, no-audio handling, VFR/nonzero origins, delayed audio waveform
alignment, approval tampering, invalid plans, source-transcript mismatch, cache integrity
and recovery after a preview failure. Tests and example approvals concern generated media.
FFmpeg-full 9.0.2 provides the libass filter; no inference/network calls occur in rendering.

These are small synthetic clips, not a long-form/4K performance benchmark. Output audio is
re-encoded AAC. HDR, multiple audio tracks and real product-demo editing quality are untested.


## M5 selected speed changes (2026-09-30)

Synthetic 10-second fixture: remove 0.2–1.8s, apply 2x to source 2–6s, mute only that speed
interval, keep all other audio at original speed. Final duration: 6.4s (40 cut frames and
50 frames saved by speed at 25fps). Source scene transition at 5s moves to 1.9s; caption
7–8s moves to 3.4–4.4s.

- Clean + captioned render wall time: 0.729s
- Same-plan cache reuse: 0.251s, both video stages reused
- Full suite: 53 passed in 17.00s; Ruff lint and formatting passed
- Both real M4 example approvals were loaded successfully without migration
- No ASR rerun, remote inference, new dependencies or media uploads

Speed tests verify overlap protection, explicit selection, rate bounds, tiny range rejection,
source/output frame mapping, stale approval rejection, legacy v1.0 payloads, real mute and
pitch-preserving audio (440Hz test tone), fractional/high rates, no-audio media, and matched
video/audio output durations. No claim is made about real-world waiting-scene detection or
natural-speech audio quality. Example: `work/m5-demo/final/preview.mp4`.

## M6 CapCut file exchange (2026-09-30)

- Full suite: 69 passed in 19.41s; Ruff lint and formatting passed.
- 16 new checks cover real render-to-bundle export, optional source/preview byte identity,
  relocation, overwrite rejection, missing installation, unapproved/incomplete/mismatched
  renders, modified media/SRT/timeline, malformed metadata, unsafe paths and symlinks.
- CLI bundle verification passed for `work/m6-capcut` (7 files, including preview).
- CapCut macOS 8.7.0 imported the existing 6.4s synthetic M5 clean video and one SRT cue.
  A separate caption track and editable `Normal speed.` text field were visible. The
  caption was displayed at 00:00:03:25 in the default 30fps project; video total displayed
  00:00:06:12. Changed caption color to black and confirmed it visibly on the white frame.
  The app displayed automatic saving. A new test project/timeline was used.
- This confirms this sample's local media/SRT import and caption styling, not native
  project reconstruction or frame-exact import across all versions. CapCut re-export,
  real product demos and long/4K sources remain untested.
- The adapter makes no network requests and does not inspect or edit CapCut internals.
  App installation detection deliberately does not set `ui_import_verified` to true.
  See `docs/capcut.md` for the observed UI sequence and file-exchange limitations.

## M7 local review UI (2026-09-30)

- Full suite: 73 passed in 21.93s; lint and format checks passed.
- HTTP tests use a real loopback server and FFmpeg. They verify missing tokens, foreign
  Host/Origin rejection, inaccessible arbitrary paths, video byte ranges, serialized jobs,
  failure recovery, analysis → selected cut/speed preview → approval → render → bundle,
  stale review IDs and changed preview plans being rejected, and approval invalidation
  after a failed new preview. The fixture shrinks from 10.0 to 6.4 seconds.
- In-app browser: analyzed the 9.4s Korean synthetic demo with local ASR, entered two English
  translations, saved captions, generated a preview, edited a caption and observed approval
  become disabled, regenerated preview, approved, rendered and exported a CapCut bundle.
  The resulting six-file bundle passed CLI verification.
- Example UI export: `work/ui/449f0320542248269c7866f613c44c83/capcut`.
  This test includes a deliberately revised test caption, not a translation-quality sample.
- No cloud API or new runtime dependency. Real customer footage, long-job cancellation,
  server-restart recovery and multi-user editing are outside this milestone.

## M8 usability and recovery (2026-10-01)

- Full suite: 78 passed in 25.33s; Ruff lint and formatting passed.
- New tests cover persisted failure/retry arguments, project ID preservation on retry,
  interrupted jobs becoming idle, authenticated video-only directory listing, saved drafts
  and media registration across workspace recreation, source-change invalidation, corrupt
  session reporting and restored approved/rendered bundles. Changed preview media also
  invalidates recovered approval.
- Browser verification: selected the synthetic clip using the local folder picker, saved
  a selected cut, restarted the server and observed that project and checked cut restored.
  Tried a missing plan, observed the error/retry control, supplied the synthetic plan and
  retried successfully. Opened the other saved project from the list.
- Checkpoint examples: `work/ui-m8/sessions/`. Access tokens are not persisted.
- Retries use fresh output directories; stage/elapsed-time reporting does not claim an ETA
  or frame-level percentage. Only explicitly saved drafts survive reload/restart.
