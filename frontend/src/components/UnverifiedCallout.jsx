import { Icon } from './Icon.jsx'

/** "We cannot verify…", why, and the two reports, for a run that verified nothing. */
export function UnverifiedCallout({ outcome, onDownload, compact = false }) {
  if (!outcome) return null
  return (
    <div className={`unverified-callout ${compact ? 'is-compact' : ''}`} role="status">
      <span className="unverified-icon"><Icon name="alert" size={compact ? 15 : 18} /></span>
      <div className="unverified-body">
        <strong>{outcome.message}</strong>
        {outcome.hasEarlierAnswers && <p>Your earlier verified answers for this topic are unchanged.</p>}
        {outcome.explanation && <p>{outcome.explanation}</p>}
        {outcome.inquiryId && (
          <div className="report-actions">
            <button className="button button-primary report-button" type="button" onClick={() => onDownload('report', outcome.inquiryId)}>
              <Icon name="download" size={15} /> Download report (PDF)
            </button>
            <button className="text-button" type="button" onClick={() => onDownload('developer-log', outcome.inquiryId)}>
              <Icon name="code" size={14} /> Developer log (JSON)
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
