import { elapsedLabel, stageDuration } from '../pipeline.js'
import { Icon } from './Icon.jsx'

export function PipelineVisualizer({ steps, running, elapsed, onCancel, cancelling, unverified = false }) {
  if (!steps.length) return null
  const failed = steps.some((step) => step.status === 'failed')
  const completed = steps.every((step) => ['complete', 'skipped'].includes(step.status))
  const active = steps.find((step) => step.status === 'active')
  // A run that verified nothing completed normally; it just has no answer.
  const noAnswer = completed && unverified
  const state = failed ? 'STOPPED' : noAnswer ? 'NO VERIFIED ANSWER' : completed ? 'COMPLETE' : running ? 'IN PROGRESS' : 'INTERRUPTED'

  return (
    <section className="pipeline-visualizer" aria-labelledby="pipeline-heading">
      <div className="pipeline-heading">
        <div>
          <span className="eyebrow small-eyebrow">LIVE RESEARCH TRACE</span>
          <h3 id="pipeline-heading">Research pipeline</h3>
        </div>
        <div className="pipeline-heading-actions">
          {running && onCancel && <button className="button button-quiet" type="button" onClick={onCancel} disabled={cancelling}>{cancelling ? 'Cancelling…' : 'Cancel research'}</button>}
          <span className={`pipeline-state ${failed ? 'is-failed' : noAnswer ? 'is-unverified' : completed ? 'is-complete' : 'is-running'}`}><i />{state}</span>
        </div>
      </div>
      <ol className="pipeline-steps">
        {steps.map((step, index) => {
          const hasProgress = Number.isFinite(step.current) && Number.isFinite(step.total) && step.total > 0
          const percent = hasProgress ? Math.min(100, Math.round((step.current / step.total) * 100)) : null
          return (
            <li className={`pipeline-step is-${step.status}`} key={step.id}>
              <span className="pipeline-marker" aria-hidden="true">{step.status === 'complete' ? <Icon name="check" size={13} /> : step.status === 'failed' ? '!' : String(index + 1).padStart(2, '0')}</span>
              <div className="pipeline-step-content">
                <div className="pipeline-step-topline"><strong>{step.title}</strong><span>{step.status === 'pending' ? 'QUEUED' : step.status.toUpperCase()}{stageDuration(step.duration_ms)}</span></div>
                <p aria-live={step.status === 'active' ? 'polite' : undefined}>{step.message}</p>
                {step.status === 'active' && hasProgress && (
                  <div className="pipeline-meter" role="progressbar" aria-label={`${step.title} progress`} aria-valuemin="0" aria-valuemax={step.total} aria-valuenow={Math.min(step.current, step.total)}>
                    <i style={{ width: `${percent}%` }} />
                  </div>
                )}
              </div>
            </li>
          )
        })}
      </ol>
      <div className="pipeline-footer">
        <span>{active ? `Current stage: ${active.title}` : failed ? 'A stage failed; see the error above.' : noAnswer ? 'Finished: no claim could be verified.' : completed ? 'All pipeline stages are settled.' : 'Waiting for pipeline updates.'}</span>
        <span>ELAPSED {elapsedLabel(elapsed)}</span>
      </div>
    </section>
  )
}
