import { Icon } from './Icon.jsx'

export function Sidebar({ activeView, onViewChange, apiOnline, accessState, llmReady, status, storageSummary }) {
  const cloudIndex = status?.vector_store_provider === 'chroma-cloud'
  return (
    <aside className="sidebar">
      <a className="brand" href="#workspace" aria-label="SpyMonk Encyclopedia home">
        <span className="brand-mark"><Icon name="compass" size={23} /></span>
        <span className="brand-type"><strong>SPYMONK</strong><small>ENCYCLOPEDIA</small></span>
      </a>

      <div className="sidebar-label">Workspace</div>
      <nav className="side-nav" aria-label="Workspace sections">
        <button className={activeView === 'research' ? 'nav-item active' : 'nav-item'} onClick={() => onViewChange('research')} type="button">
          <Icon name="search" /><span>Research desk</span><span className="nav-index">01</span>
        </button>
        <button className={activeView === 'library' ? 'nav-item active' : 'nav-item'} onClick={() => onViewChange('library')} type="button">
          <Icon name="book" /><span>Local library</span><span className="nav-index">02</span>
        </button>
      </nav>

      <div className="sidebar-divider" />
      <div className="sidebar-label">Signal check</div>
      <div className="signal-card">
        <div className="signal-heading"><span className={`signal-dot ${apiOnline ? 'online' : ''}`} /> API service</div>
        <p>{apiOnline ? 'Connected to API workspace' : accessState === 'required' ? 'Personal token required' : 'Waiting for FastAPI'}</p>
        <div className="signal-sub"><span className={`signal-dot ${llmReady ? 'online' : 'offline'}`} /> LLM credentials {llmReady ? 'ready' : 'missing'}</div>
      </div>

      <div className="sidebar-spacer" />
      <div className="privacy-note">
        <span className="privacy-stamp">{!status ? <>STORAGE<br />PENDING</> : cloudIndex ? <>CLOUD<br />INDEX</> : <>API<br />DISK</>}</span>
        <p>{storageSummary}</p>
      </div>
      <div className="sidebar-footer"><span>FIELD NOTES / 02</span><span>V2.3</span></div>
    </aside>
  )
}

export function MetricsRow({ status, hasMemory }) {
  return (
    <div className="metrics-row" aria-label="Archive overview">
      <div className="metric-card"><span className="metric-label">INDEX STATUS</span><strong>{hasMemory ? 'SEARCHABLE' : 'NOT BUILT'}</strong><span className="metric-foot"><i className={hasMemory ? 'metric-dot active' : 'metric-dot'} />{hasMemory ? 'Evidence ready to query' : 'Add a PDF or research a topic'}</span></div>
      <div className="metric-card"><span className="metric-label">TOPIC ARCHIVES</span><strong>{status?.topic_count ?? '—'}</strong><span className="metric-foot">Corroborated research records</span></div>
      <div className="metric-card"><span className="metric-label">INDEXED DOCUMENTS</span><strong>{status?.indexed_pdf_count ?? '—'}</strong><span className="metric-foot">{status?.local_pdf_count ?? '—'} PDFs stored in this workspace</span></div>
      <div className="metric-card metric-llm"><span className="metric-label">REASONING MODEL</span><strong className="model-name">{status?.llm_model || 'Awaiting configuration'}</strong><span className="metric-foot">{status?.embedding_provider || 'Local'} embeddings · source-bounded</span></div>
    </div>
  )
}

export function IndexHealthNotice({ status, onRebuild, busy }) {
  if (!status?.index_rebuild_recommended) return null
  const blocking = status.index_state === 'mismatch'
  return (
    <div className={`privacy-boundary index-health ${blocking ? 'is-blocking' : ''}`} role={blocking ? 'alert' : 'status'}>
      <Icon name="alert" size={16} />
      <p><strong>{blocking ? 'Search is unavailable until the index is rebuilt.' : 'Rebuild recommended.'}</strong> {status.index_message}</p>
      <button className="button button-secondary" type="button" onClick={onRebuild} disabled={Boolean(busy)}>{busy === 'rebuild' ? 'Rebuilding…' : 'Rebuild index'}</button>
    </div>
  )
}
