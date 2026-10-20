# React UI — visual QA

## Findings

- **Visual comparison is pending.** The React production bundle builds and the FastAPI test client serves the built page, but this session has no connected browser surface for capturing a rendered screenshot. Palette, responsive states, focus styling, and spacing therefore still need browser review against `9043432.jpg` and `9046442.jpg`.

## Implementation checklist

- [x] Replace the Streamlit page with a React/Vite research workspace.
- [x] Keep the cream, coral, lavender, and dark-ink technical-retro direction.
- [x] Use a consistent 4px spacing scale, comfortable content gutters, aligned panel padding, and 44–46px controls.
- [x] Include mobile layouts, visible keyboard focus, reduced-motion handling, and local system fonts (no remote font requests).
- [x] Distinguish local storage from text sent to configured search, LLM, or embedding providers.
- [x] Preserve research, source evidence, PDF ingestion, saved topics, re-verification, opt-in research-on-miss, and index maintenance.
- [x] `npm run build` succeeds.
- [x] FastAPI smoke checks pass for health, status, topics, and built frontend routes.
- [ ] Capture browser screenshots at desktop and mobile widths; compare the rendered result with the supplied references and refine any visible spacing issues.

Source visual references: `9043432.jpg` and `9046442.jpg` (mood boards, not page mockups).

Implementation screenshot: not captured; no connected browser surface was available.

**State:** code/build verified; rendered visual QA remains pending.
