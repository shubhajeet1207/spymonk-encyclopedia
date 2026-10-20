export function AccessPanel({ state, hasToken, open, tokenInput, onTokenInput, onSubmit, onOpen, onForget, onRetry, busy, retryInSeconds }) {
  const showForm = open || state === 'required'
  const label = state === 'required' ? 'API ACCESS REQUIRED' : state === 'ready' ? 'API ACCESS READY' : state === 'offline' ? 'API UNAVAILABLE' : 'CHECKING API ACCESS'
  const description = state === 'required'
    ? 'Enter your personal API token to load saved topics and use research tools.'
    : state === 'offline'
      ? `The API is not responding.${retryInSeconds ? ` Retrying automatically in ${retryInSeconds}s.` : ''}`
      : hasToken
        ? 'A personal token is stored for this browser tab’s session.'
        : state === 'ready'
          ? 'This API accepts your local connection without a token.'
          : 'A personal token may be required by the API.'
  return (
    <section className={`access-panel ${state === 'required' ? 'access-required' : ''}`} aria-labelledby="access-heading">
      <div className="access-panel-top">
        <div>
          <span className="eyebrow small-eyebrow">{label}</span>
          <h2 id="access-heading">{state === 'required' ? 'Unlock your workspace' : 'API access'}</h2>
          <p aria-live="polite">{description}</p>
        </div>
        <div className="access-panel-actions">
          {!showForm && <button className="button button-secondary" type="button" onClick={onOpen} disabled={Boolean(busy)}>{hasToken ? 'Change token' : 'Enter token'}</button>}
          {hasToken && <button className="text-button" type="button" onClick={onForget} disabled={Boolean(busy)}>Forget token</button>}
          {state === 'offline' && <button className="text-button" type="button" onClick={onRetry} disabled={Boolean(busy)}>Retry now</button>}
        </div>
      </div>
      {showForm && <form className="access-form" onSubmit={onSubmit}>
        <label className="field-label" htmlFor="api-token">Personal API token</label>
        <div className="access-entry-row">
          <input id="api-token" className="text-input" type="password" autoComplete="off" spellCheck="false" value={tokenInput} onChange={(event) => onTokenInput(event.target.value)} placeholder="Paste your personal token" required aria-describedby="api-token-hint" />
          <button className="button button-primary" type="submit" disabled={!tokenInput.trim() || Boolean(busy)}>{busy === 'access' ? 'Checking…' : 'Use token'}</button>
        </div>
        <p id="api-token-hint" className="field-hint">Kept only in session storage for this tab. It is never added to a URL or build variable.</p>
      </form>}
    </section>
  )
}
