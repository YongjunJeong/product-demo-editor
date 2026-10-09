# Video Agent

Develop a provider-neutral product-demo editing skill backed by the existing local Python
CLI. Codex/Claude Code hosts translation and editorial proposals; local tools validate,
compile and render them. The UI is a review surface. Preserve legacy approvals and keep
proposals, draft previews and final approval separate. Native CapCut projects and automated
visual inference remain outside the implemented scope.

- Preserve original media. Write generated data only to a unique run or work directory.
- Never upload raw video/audio/frames. The authorized host agent may read exported text
  context; explain that host inference may be remote. Do not add provider API calls or
  nested Codex/Claude processes. Model preparation only downloads weights.
- Prefer FFmpeg/ffprobe for media analysis and faster-whisper for initial local ASR.
- All analysis times are seconds relative to the first selected video frame.
- Silence and scene changes are candidates, not approved editing decisions.
- Keep analysis, planning and rendering separate. Final rendering requires a validated
  approval hash and explicit source/output mapping. Draft previews must remain unapproved.
- Cuts use source CFR frames; source captions use milliseconds. Reject cuts overlapping
  speech/captions. Speed ranges also require explicit selection and must not overlap cuts,
  speech or captions. Preserve v1.0/v1.1 approvals. Schema 1.2 whole-segment semantic cuts
  must declare their exact source IDs/protected ranges; retained speech stays guarded.
- Never interpret silence as automatic permission to delete or speed up.
- Record config, source hash, versions, measured timings and explicit skipped/failed states.
- Reuse verified checkpoints only when source, settings and implementation match.
- Keep the media CLI provider-independent. Host skills orchestrate existing commands;
  no framework or model server is needed. Do not spawn subagents unless requested.
- Use synthetic fixtures in tests; do not commit personal media, run artifacts or models.
- Read only files relevant to the change. Test the affected pipeline, not just utilities.
- Commands: `.venv/bin/pytest -q`, `.venv/bin/ruff check src tests scripts`,
  `.venv/bin/ruff format --check src tests scripts`. FFmpeg must be on PATH.
- Keep docs concise. Do not commit or push unless requested.
