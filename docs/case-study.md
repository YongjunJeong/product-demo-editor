# From subtitle automation to agent-assisted demo editing

## Problem observed

The first private human-recorded demo was about 410 seconds, with 59 Korean speech segments.
Local analysis completed in 73.8 seconds. Those results established that the media pipeline
worked, but not that it saved editing effort. The user still had to supply English translations,
and the reviewed result selected zero cuts and zero speed changes.

A roughly 33-second detected silence also disappeared from candidates because one boundary
slightly overlapped protected speech. The original conservative implementation rejected the
entire interval. The UI compounded the problem: a preview took several minutes but showed
status only near the top of a long page, making a working button appear unresponsive.

The source video, transcript, product names and local paths are not published here. These
aggregate measurements are one development case, not a general performance benchmark.

## What changed

Media processing stayed in Python/FFmpeg. A Codex/Claude Code skill now handles translation
and editing proposals. The host model reads the exported text and returns a response that
the CLI validates. It does not edit the timeline or write FFmpeg commands.

The skill's output includes reasons, uncertain interpretations and screen-review flags.
The importer binds it to source evidence, checks complete translation IDs and compiles
selected edits on a CFR grid. Approval covers the exact plan and compiled output timeline.
The UI lets the user review proposals. Translation runs in the existing agent conversation,
so the editor does not need its own model server.

## Concrete corrections

1. Subtract protected ranges from silence intervals, preserving eligible safe interiors.
2. Model whole-segment speech deletion explicitly, with declared IDs and protected ranges.
   Retained neighboring speech remains guarded; speedups cannot override speech protection.
3. Preserve all source translations in the draft. Selecting a semantic cut removes only
   its associated output captions; rejecting it restores the original speech/captions.
4. Show render stage and elapsed time beside the preview button, including failures.
5. Supply a public synthetic demo and tests that exercise compilation, actual rendering,
   approval binding and verified file exchange without private recordings.

## Tradeoffs and evaluation

The workflow requires a compatible agent host. The local UI handles review and rendering,
but does not generate translations on its own. The host may use a remote model, while
media processing remains local. Both hosts exchange the same JSON files with the CLI.

Text-only evidence makes editorial proposals possible but does not prove screen-action
preservation. A shorter video still needs to show every essential step.
Further evaluation should track translation corrections, proposal acceptance,
screen-action loss and active human review time on repeated real recordings.

The synthetic example uses authored input and an authored model response. It exercises
validation and rendering without calling an LLM. Codex is the first implemented host;
Claude Code compatibility is an instruction/CLI design until independently live-tested.

## First host-agent draft check

On the same private 410-second recording, the current Codex conversation generated all
59 English translation entries without user form input. Two waiting-speed proposals and
one trailing-silence cut compiled and rendered to 380.73 seconds (about 29 seconds shorter).
The preview remains unapproved, with visual-review flags and uncertain ASR terminology
listed. No semantic speech removal was proposed without evidence of redundancy.

The rendered video/audio duration checks passed and the source hash stayed unchanged.
This is one host-agent workflow check, not a translation accuracy score or proof of human
time saved. Generation/render artifacts and transcript contents remain private under work/.
