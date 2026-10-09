# Agent-assisted product-demo editing

Codex or Claude Code is the host reasoning engine. There is no nested agent process,
provider SDK or model-routing server. The skill is provider-neutral workflow guidance;
FFmpeg, faster-whisper and the Python CLI remain the execution layer.

## Install

From the repository root after preparing `.venv`:

```sh
python scripts/install_skill.py --host codex
# Optional alternative: --host claude, or --host both.
```

This links the maintained skill to the selected host's user skill directory. It preserves
existing skills and never replaces a conflicting path. Moving the checkout breaks the
link; relink it after verifying its old destination. Windows symlink creation may require
OS permissions; manual skill deployment is an alternative. Codex is validated locally;
Claude Code instructions share the same CLI but have not been live-tested here.
Its documented personal skill path and symlink support are described in the
[Claude Code skill documentation](https://code.claude.com/docs/en/skills).
Codex skill structure follows the [OpenAI documentation](https://developers.openai.com/plugins/build/skills).

Example request in the host:

> Use $product-demo-editor to edit my Korean product demo with English captions.
> Preserve the necessary UI steps and propose cuts for repetition and waiting time.

Read the [skill](../skills/product-demo-editor/SKILL.md) and its
[exchange contract](../skills/product-demo-editor/references/exchange.md).

## Boundaries

- Raw media, transcription and rendering stay local. Preparing ASR weights downloads models.
- Reading text context into Codex/Claude follows that host's inference and data policies;
  this is **not** a fully offline LLM architecture. There is no additional provider call
  from this Python application. Host usage/cost is unknown to the local CLI.
- Request context contains transcript text, glossary and verified source times; raw media,
  original local paths and model cache paths are excluded. Local plans retain source paths.
- Transcript-only reasoning cannot verify loading, UI actions or screenshot visibility.
  Such edits carry a visual-review flag. Automatic visual inference is not implemented.
- Proposed edits stay pending. An unapproved preview can show the combined proposal;
  final export requires the approved exact plan and compiled timeline hash.

## Schema 1.2 speech edits

Whole-segment removal is an explicit exception to the original speech guard. Each semantic
cut binds source segment IDs, their original protected ranges, and a rationale. Only the
declared ranges and wholly associated captions can overlap it; retained speech/captions
remain protected. Speeds never receive a speech exception. A selected semantic cut drops
its captions, while a rejected proposal retains both speech and captions. This validates
structure, not the model's judgement about whether speech is redundant.

Boundary-overlapping silence now subtracts protected intervals, leaving eligible interior
candidates rather than discarding the whole wait. CFR quantization stays conservative.
Existing schema 1.0/1.1 approval payloads retain their previous serialization.

## Reproducible demo and evidence

```sh
python scripts/demo_agent.py --output work/showcase
```

It generates a 10-second black/white video, uses **authored synthetic annotations and an
authored response**, validates a speech-removal proposal, remaps captions and creates an
unapproved video. It does not test ASR or LLM semantic quality. All generated media,
absolute paths, transcripts and agent responses stay under ignored `work/`.

Automated tests also cover request/artifact mismatch, unknown/duplicate IDs, unsafe
neighbor speech removal, rejected-proposal restoration, duration/audio checks and verified
CapCut file exchange. Personal recordings are never part of the test suite or public demo.

Actual quality evaluation should measure translation corrections, rejected editing
proposals, screen-action loss, review time and final editing time. Duration reduction alone
is not a quality score. No model comparison or productivity percentage is claimed.
