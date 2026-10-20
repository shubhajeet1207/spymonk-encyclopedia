import { unverifiedOutcome } from '../outcome.js'
import { Icon } from './Icon.jsx'
import { PipelineVisualizer } from './PipelineVisualizer.jsx'
import { SourceList } from './SourceList.jsx'
import { UnverifiedCallout } from './UnverifiedCallout.jsx'

function ResearchOutcome({ archive, onDownload }) {
  const saved = archive.memory_updated
  return (
    <div className="research-outcome">
      <div className="outcome-banner">
        <div><span className="eyebrow small-eyebrow">{saved ? 'ARCHIVE UPDATED' : 'NO VERIFIED ANSWER'}</span><strong>{archive.topic}</strong></div>
        <div className="outcome-count"><b>{archive.claims?.length || 0}</b><span>{saved ? 'corroborated' : 'verified'}</span></div>
      </div>
      <UnverifiedCallout outcome={unverifiedOutcome(archive)} onDownload={onDownload} />
      {saved && archive.held_back_claims > 0 && <p className="empty-inline">{archive.held_back_claims} claim{archive.held_back_claims === 1 ? ' was' : 's were'} held back because a cross-check could not finish. They are listed below as “incomplete”; researching again can verify them.</p>}
      {archive.claims?.length > 0
        ? (
          <div className="claim-section">
            <h3>{saved ? 'Promoted to searchable memory' : 'Corroborated in this attempt — not saved'}</h3>
            <ul className="claim-list">
              {archive.claims.map((claim) => (
                <li key={claim.id}><Icon name={saved ? 'check' : 'alert'} size={15} /><span>{claim.claim}<small>{(claim.supporting_origins || []).join(' · ')}</small></span></li>
              ))}
            </ul>
          </div>
        )
        : saved && <p className="empty-inline">No claims met the archive’s cross-origin validation threshold this time.</p>}
      {archive.unresolved_claims?.length > 0 && (
        <details className="unresolved-disclosure">
          <summary><Icon name="alert" size={15} /> {archive.unresolved_claims.length} {saved ? 'unresolved claims — review only' : 'claims the AI found, none verified'}</summary>
          <ul>{archive.unresolved_claims.map((claim) => <li key={claim.id}><span className="evidence-tag">{claim.validation_status}</span>{claim.claim}</li>)}</ul>
        </details>
      )}
      {archive.sources?.length > 0 && (
        <details className="source-disclosure">
          <summary><Icon name="book" size={16} /> Collected source set <span className="source-count">{archive.sources.length}</span></summary>
          <ul className="collected-list">
            {archive.sources.map((source) => <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title || source.url}</a><span>{source.source_type} · {source.origin}</span></li>)}
          </ul>
        </details>
      )}
    </div>
  )
}

export function ResearchPanel({
  topic, onTopicChange, refreshTopic, onRefreshChange, canRun, busy,
  onSubmit, onPlan, searchPlan, research, onDownload,
}) {
  const { pipeline, running, elapsed, result, cancel, cancelling } = research
  return (
    <section className="panel research-panel" aria-labelledby="research-heading">
      <div className="panel-heading">
        <div className="panel-icon coral-icon"><Icon name="spark" size={19} /></div>
        <div><div className="eyebrow small-eyebrow">PUBLIC WEB / CROSS-ORIGIN REVIEW</div><h2 id="research-heading">Research a topic</h2></div>
        <span className="panel-number">01 / 02</span>
      </div>
      <p className="panel-copy">Collect public articles and PDFs, verify quoted evidence, and keep unresolved claims out of normal answers.</p>
      <form className="research-form" onSubmit={onSubmit}>
        <label className="field-label" htmlFor="research-topic">Topic or question</label>
        <div className="input-action-row">
          <input id="research-topic" className="text-input" value={topic} onChange={(event) => onTopicChange(event.target.value)} placeholder="e.g. Chanakya Neeti on work ethics" maxLength={500} />
          <button className="button button-primary" type="submit" disabled={!canRun || !topic.trim() || Boolean(busy)}>
            {busy === 'research' ? <><span className="button-spinner" /> Researching</> : <>Research <Icon name="arrow" size={16} /></>}
          </button>
        </div>
        <div className="form-options">
          <label className="check-option"><input type="checkbox" checked={refreshTopic} onChange={(event) => onRefreshChange(event.target.checked)} /><span>Refresh sources even if memory already answers</span></label>
          <span className="field-hint">Requires configured LLM credentials</span>
        </div>
        <div className="plan-actions">
          <button className="button button-secondary" type="button" onClick={onPlan} disabled={!canRun || !topic.trim() || Boolean(busy)}>
            {busy === 'planning' ? <><span className="button-spinner" /> Preparing plan</> : <>Prepare search plan</>}
          </button>
          <span className="field-hint">Optional LLM step; review queries before source collection.</span>
        </div>
      </form>
      {busy === 'planning' && <div className="working-callout"><span className="working-pulse" /><div><strong>Preparing search queries</strong><p>The configured LLM is planning targeted searches. This step does not fetch or analyze web sources.</p></div></div>}
      {(running || pipeline.length > 0) && <PipelineVisualizer steps={pipeline} running={running} elapsed={elapsed} onCancel={cancel} cancelling={cancelling} unverified={Boolean(unverifiedOutcome(result?.archive))} />}
      {searchPlan?.topic === topic.trim() && (
        <div className="search-plan-preview" aria-live="polite">
          <div className="search-plan-heading"><strong>{searchPlan.fallback_used ? 'Baseline search plan' : 'LLM search plan'}</strong><span>{searchPlan.queries.length} queries · {searchPlan.planner}</span></div>
          <ol>{searchPlan.queries.map((query, index) => <li key={`${index}-${query}`}><span>{String(index + 1).padStart(2, '0')}</span><code>{query}</code></li>)}</ol>
        </div>
      )}
      {result?.answer && <div className="answer-card"><div className="answer-label"><Icon name="check" size={15} /> {result.cached ? 'FOUND IN YOUR ARCHIVE' : 'ANSWER FROM NEW RESEARCH'}</div><p>{result.answer}</p><SourceList sources={result.sources} /></div>}
      {result?.archive && <ResearchOutcome archive={result.archive} onDownload={onDownload} />}
    </section>
  )
}
