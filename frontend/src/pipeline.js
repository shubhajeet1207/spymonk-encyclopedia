export const PIPELINE_STEPS = [
  ['memory', 'Check archive'],
  ['retrieve', 'Search saved evidence'],
  ['reverify', 'Recheck source quotes'],
  ['compose', 'Prepare archive answer'],
  ['plan', 'Plan searches'],
  ['search', 'Search web'],
  ['collect', 'Collect sources'],
  ['extract', 'Extract claims'],
  ['verify', 'Cross-check evidence'],
  ['save', 'Save findings'],
  ['answer', 'Answer from new evidence'],
]

export function initialPipeline() {
  return PIPELINE_STEPS.map(([id, title]) => ({ id, title, status: 'pending', message: 'Waiting to start.' }))
}

/** Merge one server stage event into the step list. */
export function applyPipelineEvent(steps, event) {
  return steps.map((step) => (step.id === event.step ? { ...step, ...event } : step))
}

/**
 * Record a failure on one stage: the stage the server already reported as
 * failed, otherwise the one running (or next to run). Never marks a second stage.
 */
export function failCurrentStep(steps, message) {
  let index = steps.findIndex((step) => step.status === 'failed')
  if (index < 0) index = steps.findIndex((step) => step.status === 'active' || step.status === 'pending')
  if (index < 0) return steps
  return steps.map((step, position) => (position === index ? { ...step, status: 'failed', message } : step))
}

export function stageDuration(ms) {
  if (!Number.isFinite(ms)) return ''
  return ms < 1000 ? ` · ${Math.round(ms)} ms` : ` · ${(ms / 1000).toFixed(1)} s`
}

export function elapsedLabel(seconds) {
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}
