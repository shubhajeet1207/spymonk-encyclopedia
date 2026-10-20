import { Icon } from './Icon.jsx'

export function SourceList({ sources = [] }) {
  if (!sources.length) return null
  return (
    <details className="source-disclosure">
      <summary><Icon name="book" size={16} /> Evidence and sources <span className="source-count">{sources.length}</span></summary>
      <div className="source-list">
        {sources.map((source, index) => (
          <article className="source-card" key={`${source.url || source.source}-${index}`}>
            <div className="source-card-heading">
              <strong>{source.source || source.source_title || 'Source'}</strong>
              <span className={`evidence-tag ${source.validation_status === 'local_source' ? 'tag-local' : ''}`}>
                {source.validation_status || 'source'}
              </span>
            </div>
            <p className="source-meta">
              {source.origin || source.source_type || 'archive'}
              {source.page ? ` · page ${source.page}` : ''}
              {source.supporting_origins ? ` · corroborated by ${source.supporting_origins}` : ''}
            </p>
            {source.excerpt && <blockquote>{source.excerpt}</blockquote>}
            {source.url && <a href={source.url} target="_blank" rel="noreferrer">Open original source <span aria-hidden="true">↗</span></a>}
            {(source.evidence || []).map((item, evidenceIndex) => (
              <div className="evidence-line" key={`${item.source_url}-${evidenceIndex}`}>
                <span className="evidence-verdict">{item.verdict}</span>
                {item.origin && <span>{item.origin}</span>}
                {item.quote && <p>“{item.quote}”</p>}
              </div>
            ))}
          </article>
        ))}
      </div>
    </details>
  )
}
