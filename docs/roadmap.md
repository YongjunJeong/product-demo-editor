# Next steps

## Current workflow

The M1–M8 local pipeline is now the execution backend for an agent skill. Codex/Claude Code
write translations and editing proposals. `agent-export` and `agent-import` exchange JSON
files tied to the source analysis.
The first skill generates English-caption drafts and explained waiting/speech-removal proposals.
Schema 1.2 adds explicit whole-segment speech-removal metadata while preserving older approvals.

The local UI handles review and rendering; it does not launch an LLM. A synthetic demo
and GitHub Actions workflow support a public repository without publishing private footage.

## Next: real workflow evaluation

Use multiple recordings to measure translation corrections, proposal rejection reasons,
screen-action loss, active review time and final completion time. Compare with the user's
previous workflow using the same content. Record Codex host usage separately from local tool
metrics; do not infer a productivity percentage from video-duration reduction.

## Improvements to evaluate

- Better subtitle timing/readability and term handling, based on actual corrections.
- Optional authorized visual evidence for uncertain waits and caption obstruction.
- Faster draft-only rendering and verified render reuse if repeated previews dominate waiting.
- Live Claude Code validation of the same skill/CLI contract.

Native CapCut timelines, arbitrary partial-sentence cuts and standalone consumer packaging
are not planned for the current version.
