import { useEffect, useRef } from 'react'

import { unverifiedOutcome } from '../outcome.js'
import { Icon } from './Icon.jsx'
import { SourceList } from './SourceList.jsx'
import { UnverifiedCallout } from './UnverifiedCallout.jsx'

function Message({ message, onDownload }) {
  const isUser = message.role === 'user'
  const unverified = message.researched ? unverifiedOutcome(message.archive) : null
  return (
    <article className={`chat-message ${message.role} ${message.error ? 'message-error' : ''}`}>
      <div className="message-avatar">{isUser ? 'YOU' : 'SM'}</div>
      <div className="message-body">
        <span className="message-role">{isUser ? 'YOUR QUESTION' : 'SPYMONK ARCHIVE'}{message.researched && ' · WEB RESEARCHED'}</span>
        {unverified
          ? <UnverifiedCallout outcome={unverified} onDownload={onDownload} compact />
          : <p>{message.content}</p>}
        {message.archive && <div className="research-note">{message.archive.memory_updated ? 'New review' : 'Research not saved'}: {message.archive.claims_count} corroborated · {message.archive.unresolved_count} unresolved</div>}
        <SourceList sources={message.sources} />
      </div>
    </article>
  )
}

export function ChatPanel({
  messages, question, onQuestionChange, onSubmit, busy, apiOnline, llmReady, hasMemory,
  reverify, onReverifyChange, researchOnMiss, onResearchOnMissChange, onDownload,
}) {
  const messageEnd = useRef(null)
  useEffect(() => {
    messageEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [messages, busy])

  return (
    <section className="panel chat-panel" aria-labelledby="ask-heading">
      <div className="panel-heading chat-heading">
        <div className="panel-icon ink-icon"><Icon name="search" size={19} /></div>
        <div><div className="eyebrow small-eyebrow">QUERY YOUR EVIDENCE</div><h2 id="ask-heading">Ask the encyclopedia</h2></div>
        <span className={`index-pill ${hasMemory ? 'ready' : ''}`}><i />{hasMemory ? 'INDEX READY' : 'NO INDEX'}</span>
      </div>
      {!hasMemory && <div className="empty-chat"><span className="empty-icon"><Icon name="book" size={22} /></span><div><strong>Your archive starts with evidence</strong><p>Research a topic or add a PDF above. Answers are generated only from stored sources.</p></div></div>}
      {messages.length > 0 && (
        <div className="message-list" aria-live="polite">
          {messages.map((message, index) => <Message key={`${message.role}-${index}`} message={message} onDownload={onDownload} />)}
          {busy === 'ask' && <div className="assistant-working"><span className="working-pulse" />Checking stored evidence and re-validating source quotes…</div>}
          <div ref={messageEnd} />
        </div>
      )}
      <form className="ask-form" onSubmit={onSubmit}>
        <label className="visually-hidden" htmlFor="ask-question">Ask a question</label>
        <input id="ask-question" className="text-input" value={question} onChange={(event) => onQuestionChange(event.target.value)} placeholder="Ask about something in your archive…" maxLength={2000} disabled={!apiOnline} />
        <button className="send-button" type="submit" aria-label="Ask question" disabled={!apiOnline || !llmReady || !question.trim() || Boolean(busy) || (!hasMemory && !researchOnMiss)}><Icon name="arrow" /></button>
      </form>
      <div className="chat-controls">
        <label className="check-option"><input type="checkbox" checked={reverify} onChange={(event) => onReverifyChange(event.target.checked)} /><span>Re-check source quotes on every question</span></label>
        <label className="check-option"><input type="checkbox" checked={researchOnMiss} onChange={(event) => onResearchOnMissChange(event.target.checked)} /><span>Research the web if memory cannot answer</span></label>
      </div>
      {!llmReady && <p className="configuration-hint"><Icon name="alert" size={15} /> Add <code>LLM_API_KEY</code> to your local <code>.env</code> file to ask or research.</p>}
      <p className="disclaimer">Questions and relevant source excerpts may be sent to the configured LLM or embedding provider. Evidence indicates what a source says; it is not a guarantee that the source is correct.</p>
    </section>
  )
}
