import { Icon } from './Icon.jsx'

function topicNote(item) {
  const indexed = item.indexed_claims ?? item.claims
  if (indexed >= item.claims) return `${indexed} answerable claims`
  return `${indexed} of ${item.claims} claims searchable · rebuild the index to restore the rest`
}

function shortDate(value) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function UnverifiedQuestions({ items, onDownload }) {
  return (
    <section className="side-panel inquiry-panel">
      <div className="side-panel-heading"><div><span className="eyebrow small-eyebrow">NEEDS BETTER EVIDENCE</span><h2>Unverified questions</h2></div><span className="topic-total">{items.length.toString().padStart(2, '0')}</span></div>
      {items.length
        ? (
          <ul className="inquiry-list">
            {items.map((item) => (
              <li key={item.id}>
                <div className="inquiry-text"><strong title={item.question}>{item.question}</strong><small>{item.reason_label}{shortDate(item.created_at) && ` · ${shortDate(item.created_at)}`}</small></div>
                <div className="inquiry-actions">
                  <button className="icon-button" type="button" onClick={() => onDownload('report', item.id)} aria-label={`Download the report for: ${item.question}`} title="Download report (PDF)"><Icon name="download" size={15} /></button>
                  <button className="icon-button" type="button" onClick={() => onDownload('developer-log', item.id)} aria-label={`Download the developer log for: ${item.question}`} title="Developer log (JSON)"><Icon name="code" size={15} /></button>
                </div>
              </li>
            ))}
          </ul>
        )
        : <div className="empty-topics"><span>00</span><p>Questions research could not verify appear here, each with a downloadable report.</p></div>}
    </section>
  )
}

export function ArchiveSidebar({ topics, inquiries = [], onSelectTopic, onStartNote, apiOnline, hasMemory, busy, onClear, onRebuild, onDownload }) {
  return (
    <aside className="right-column" aria-label="Archive details">
      <section className="side-panel topic-panel">
        <div className="side-panel-heading"><div><span className="eyebrow small-eyebrow">RESEARCH NOTEBOOK</span><h2>Saved topics</h2></div><span className="topic-total">{topics.length.toString().padStart(2, '0')}</span></div>
        {topics.length
          ? (
            <ul className="topic-list">
              {topics.map((item, index) => (
                <li key={item.topic}>
                  <button type="button" onClick={() => onSelectTopic(item.topic)}>
                    <span className="topic-ordinal">{String(index + 1).padStart(2, '0')}</span>
                    <span className="topic-name">{item.topic}<small>{topicNote(item)}</small></span>
                    <span className="topic-arrow">↗</span>
                  </button>
                </li>
              ))}
            </ul>
          )
          : <div className="empty-topics"><span>01</span><p>Your reviewed topics will appear here for quick recall.</p></div>}
        <button className="text-button notebook-button" type="button" onClick={onStartNote}>Start a research note <Icon name="arrow" size={15} /></button>
      </section>

      <UnverifiedQuestions items={inquiries} onDownload={onDownload} />

      <section className="side-panel method-panel">
        <div className="method-top"><div className="method-seal"><Icon name="check" size={20} /></div><span className="eyebrow small-eyebrow">METHOD / 03</span></div>
        <h2>Evidence before answers.</h2>
        <p>Web claims need quote-level support and independent origins. Conflicts stay visible for review instead of being flattened into a false consensus.</p>
        <div className="method-steps"><div><span>01</span><p>Retrieve from local memory first</p></div><div><span>02</span><p>Cross-check independent sources</p></div><div><span>03</span><p>Show citations with the answer</p></div></div>
      </section>

      <section className="side-panel settings-panel">
        <div className="side-panel-heading"><div><span className="eyebrow small-eyebrow">INDEX MAINTENANCE</span><h2>Workspace tools</h2></div><Icon name="compass" size={18} /></div>
        <p>Rebuilding re-indexes your stored PDFs and saved research from scratch. Clearing removes only the search index; files and research records are kept.</p>
        <div className="workspace-tool-actions">
          <button className="button button-quiet" type="button" onClick={onRebuild} disabled={!apiOnline || Boolean(busy)}><Icon name="refresh" size={16} /> {busy === 'rebuild' ? 'Rebuilding…' : 'Rebuild searchable index'}</button>
          <button className="button button-quiet" type="button" onClick={onClear} disabled={!apiOnline || !hasMemory || Boolean(busy)}><Icon name="trash" size={16} /> {busy === 'clear' ? 'Clearing…' : 'Clear searchable index'}</button>
        </div>
      </section>
    </aside>
  )
}
