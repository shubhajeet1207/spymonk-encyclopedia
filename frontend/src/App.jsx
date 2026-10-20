import { useCallback, useRef, useState } from 'react'

import { api, ApiAuthenticationError } from './api.js'
import { AccessPanel } from './components/AccessPanel.jsx'
import { ArchiveSidebar } from './components/ArchiveSidebar.jsx'
import { ChatPanel } from './components/ChatPanel.jsx'
import { Icon } from './components/Icon.jsx'
import { LibraryPanel } from './components/LibraryPanel.jsx'
import { Notice } from './components/Notice.jsx'
import { ResearchPanel } from './components/ResearchPanel.jsx'
import { IndexHealthNotice, MetricsRow, Sidebar } from './components/WorkspaceChrome.jsx'
import { useResearchJob } from './hooks/useResearchJob.js'
import { useWorkspace } from './hooks/useWorkspace.js'
import { researchNotice, unverifiedOutcome } from './outcome.js'
import { privacySummary } from './privacy.js'

function uploadMessage(result) {
  const parts = []
  if (result.file_count) {
    parts.push(`Added ${result.file_count} PDF${result.file_count === 1 ? '' : 's'} and indexed ${result.chunk_count} searchable chunks.`)
  }
  if (result.duplicates?.length) {
    parts.push(`Already in your library, so skipped: ${result.duplicates.join(', ')}.`)
  }
  return parts.join(' ') || 'No new PDFs were added.'
}

function App() {
  const [activeView, setActiveView] = useState('research')
  const [topic, setTopic] = useState('')
  const [question, setQuestion] = useState('')
  const [selectedFiles, setSelectedFiles] = useState([])
  const [refreshTopic, setRefreshTopic] = useState(false)
  const [reverify, setReverify] = useState(true)
  const [researchOnMiss, setResearchOnMiss] = useState(false)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState(null)
  const [searchPlan, setSearchPlan] = useState(null)
  const [messages, setMessages] = useState([])
  const [hasSessionToken, setHasSessionToken] = useState(Boolean(api.getToken()))
  const [tokenPanelOpen, setTokenPanelOpen] = useState(false)
  const [tokenInput, setTokenInput] = useState('')
  const actionInFlight = useRef(false)
  const fileInput = useRef(null)
  const research = useResearchJob()
  const resetResearch = research.reset

  const clearPrivateWorkspace = useCallback(() => {
    setTopic('')
    setQuestion('')
    setSelectedFiles([])
    setSearchPlan(null)
    setMessages([])
    resetResearch()
    if (fileInput.current) fileInput.current.value = ''
  }, [resetResearch])

  const onAccessDenied = useCallback((error) => {
    api.clearToken()
    setHasSessionToken(false)
    setTokenInput('')
    clearPrivateWorkspace()
    setTokenPanelOpen(true)
    setNotice({ kind: 'error', message: error.message })
  }, [clearPrivateWorkspace])

  const workspace = useWorkspace({ onAccessDenied })
  const { status, topics, inquiries, accessState } = workspace

  /** Run one user action at a time, routing auth failures to the access panel. */
  async function runAction(name, action, onError) {
    if (actionInFlight.current) return
    actionInFlight.current = true
    setBusy(name)
    setNotice(null)
    try {
      await action()
    } catch (error) {
      if (error instanceof ApiAuthenticationError) {
        workspace.reset()
        workspace.setAccessState('required')
        onAccessDenied(error)
      } else if (onError) {
        onError(error)
      } else {
        setNotice({ kind: 'error', message: error.message })
      }
    } finally {
      actionInFlight.current = false
      setBusy('')
    }
  }

  /** Download one of an unverified question's reports without blocking other actions. */
  async function downloadInquiry(kind, inquiryId) {
    try {
      if (kind === 'report') await api.downloadReport(inquiryId)
      else await api.downloadDeveloperLog(inquiryId)
    } catch (error) {
      if (error instanceof ApiAuthenticationError) {
        workspace.reset()
        workspace.setAccessState('required')
        onAccessDenied(error)
      } else {
        setNotice({ kind: 'error', message: `The report could not be downloaded: ${error.message}` })
      }
    }
  }

  function retryApiAccess() {
    return runAction('access', async () => {
      const error = await workspace.load()
      if (!error) {
        setTokenPanelOpen(false)
        setHasSessionToken(Boolean(api.getToken()))
        setNotice({ kind: 'success', message: 'API workspace connected.' })
      } else if (!(error instanceof ApiAuthenticationError)) {
        setNotice({ kind: 'error', message: error.message })
      }
    })
  }

  function submitApiToken(event) {
    event.preventDefault()
    if (actionInFlight.current) return
    try {
      api.setToken(tokenInput)
    } catch (error) {
      setNotice({ kind: 'error', message: error.message })
      return
    }
    workspace.reset()
    setHasSessionToken(true)
    setTokenInput('')
    retryApiAccess()
  }

  function forgetApiToken() {
    if (actionInFlight.current) return
    api.clearToken()
    setHasSessionToken(false)
    setTokenInput('')
    clearPrivateWorkspace()
    workspace.reset()
    workspace.setAccessState('checking')
    setTokenPanelOpen(false)
    retryApiAccess()
  }

  function runResearch(event) {
    event.preventDefault()
    if (!topic.trim()) return
    const activePlan = searchPlan?.topic === topic.trim() ? searchPlan : null
    return runAction('research', async () => {
      const result = await research.start({
        topic: topic.trim(),
        refresh: refreshTopic,
        reverify,
        searchQueries: activePlan?.queries || [],
      })
      if (!result.cached && result.search_plan && !activePlan) {
        setSearchPlan({ topic: topic.trim(), ...result.search_plan })
      }
      setNotice(researchNotice(result))
      await workspace.refresh()
    }, (error) => {
      setNotice({ kind: error.name === 'AbortError' ? 'success' : 'error', message: error.message })
    })
  }

  function prepareSearchPlan() {
    if (!topic.trim()) return
    return runAction('planning', async () => {
      const plan = await api.planSearch({ topic: topic.trim() })
      setSearchPlan(plan)
      setNotice({
        kind: 'success',
        message: `LLM prepared ${plan.queries.length} search queries. Review them below, then run the research step.`,
      })
    })
  }

  function submitQuestion(event) {
    event.preventDefault()
    const prompt = question.trim()
    if (!prompt || actionInFlight.current) return
    setQuestion('')
    setMessages((current) => [...current, { role: 'user', content: prompt }])
    return runAction('ask', async () => {
      const result = await api.ask({ question: prompt, reverify, researchOnMiss })
      setMessages((current) => [...current, {
        role: 'assistant',
        content: result.answer,
        sources: result.sources,
        researched: result.researched,
        archive: result.archive,
      }])
      if (result.researched) {
        await workspace.refresh()
        const unverified = unverifiedOutcome(result.archive)
        setNotice(unverified
          ? { kind: 'error', message: unverified.message }
          : { kind: 'success', message: 'No answer was in memory, so opted-in web research updated the archive before one retry.' })
      }
    }, (error) => {
      setMessages((current) => [...current, { role: 'assistant', content: `The request could not be completed: ${error.message}`, error: true }])
    })
  }

  function addPdfs(event) {
    event.preventDefault()
    if (!selectedFiles.length) return
    return runAction('upload', async () => {
      const result = await api.addDocuments(selectedFiles)
      setNotice({ kind: 'success', message: uploadMessage(result) })
      setSelectedFiles([])
      if (fileInput.current) fileInput.current.value = ''
      await workspace.refresh()
    })
  }

  function clearIndex() {
    if (!window.confirm('Clear the searchable vector index? Stored PDFs and research records are kept, and you can rebuild the index later.')) return
    return runAction('clear', async () => {
      const result = await api.clearMemory()
      setMessages([])
      research.reset()
      setNotice({ kind: 'success', message: result.message })
      await workspace.refresh()
    })
  }

  function rebuildIndex() {
    if (!window.confirm('Rebuild the searchable index from stored PDFs and saved research? This can take a few minutes; the current index keeps working until the new one is ready.')) return
    return runAction('rebuild', async () => {
      const result = await api.rebuildIndex()
      const skipped = result.skipped_pdfs?.length ? ` ${result.skipped_pdfs.length} PDF(s) had no readable text and were skipped.` : ''
      const duplicates = result.duplicate_pdfs?.length ? ` ${result.duplicate_pdfs.length} duplicate copies were ignored.` : ''
      setNotice({ kind: 'success', message: `Index rebuilt: ${result.indexed_pdfs.length} PDFs and ${result.claims} research claims are searchable.${skipped}${duplicates}` })
      await workspace.refresh()
    })
  }

  function selectTopic(name) {
    setTopic(name)
    setActiveView('research')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const apiOnline = Boolean(status?.api_online)
  const hasMemory = Boolean(status?.index_exists)
  const llmReady = Boolean(status?.llm_configured)
  const storageSummary = privacySummary(status)

  return (
    <div className="app-shell">
      <Sidebar activeView={activeView} onViewChange={setActiveView} apiOnline={apiOnline} accessState={accessState} llmReady={llmReady} status={status} storageSummary={storageSummary} />

      <main id="workspace" className="main-shell">
        <header className="topbar">
          <div className="window-label"><span className="window-lights"><i /><i /><i /></span><span>PERSONAL RESEARCH ARCHIVE</span></div>
          <div className="topbar-right"><span className="connection-status"><i className={apiOnline ? 'connected' : ''} />{apiOnline ? 'API CONNECTED' : accessState === 'required' ? 'ACCESS REQUIRED' : 'API OFFLINE'}</span><span className="topbar-version">EDITION 02 · 2026</span></div>
        </header>

        <div className="page-content">
          <section className="page-intro">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> KNOWLEDGE, WITH RECEIPTS</div>
              <h1>Research that<br className="desktop-break" /> shows its work.</h1>
              <p className="intro-copy">Build a personal archive from the open web and your own documents. Every answer points back to its evidence.</p>
            </div>
            <div className="intro-stamp" aria-label="Evidence first"><span>EST.</span><strong>SM</strong><span>EVIDENCE<br />FIRST</span></div>
          </section>

          <Notice notice={notice} onDismiss={() => setNotice(null)} />

          <AccessPanel state={accessState} hasToken={hasSessionToken} open={tokenPanelOpen} tokenInput={tokenInput} onTokenInput={setTokenInput} onSubmit={submitApiToken} onOpen={() => setTokenPanelOpen(true)} onForget={forgetApiToken} onRetry={retryApiAccess} busy={busy} retryInSeconds={workspace.retryIn} />

          <MetricsRow status={status} hasMemory={hasMemory} />
          <IndexHealthNotice status={status} onRebuild={rebuildIndex} busy={busy} />

          <div className="privacy-boundary"><Icon name="alert" size={16} /><p><strong>Storage and processing boundary:</strong> {storageSummary}</p></div>

          <div className="workspace-grid">
            <section className="primary-column">
              {activeView === 'research'
                ? (
                  <ResearchPanel
                    topic={topic}
                    onTopicChange={(value) => { setTopic(value); setSearchPlan(null) }}
                    refreshTopic={refreshTopic}
                    onRefreshChange={setRefreshTopic}
                    canRun={apiOnline && llmReady}
                    busy={busy}
                    onSubmit={runResearch}
                    onPlan={prepareSearchPlan}
                    searchPlan={searchPlan}
                    research={research}
                    onDownload={downloadInquiry}
                  />
                )
                : (
                  <LibraryPanel selectedFiles={selectedFiles} onFilesChange={setSelectedFiles} onSubmit={addPdfs} apiOnline={apiOnline} busy={busy} status={status} inputRef={fileInput} />
                )}

              <ChatPanel
                messages={messages}
                question={question}
                onQuestionChange={setQuestion}
                onSubmit={submitQuestion}
                busy={busy}
                apiOnline={apiOnline}
                llmReady={llmReady}
                hasMemory={hasMemory}
                reverify={reverify}
                onReverifyChange={setReverify}
                researchOnMiss={researchOnMiss}
                onResearchOnMissChange={setResearchOnMiss}
                onDownload={downloadInquiry}
              />
            </section>

            <ArchiveSidebar
              topics={topics}
              inquiries={inquiries}
              onDownload={downloadInquiry}
              onSelectTopic={selectTopic}
              onStartNote={() => setActiveView('research')}
              apiOnline={apiOnline}
              hasMemory={hasMemory}
              busy={busy}
              onClear={clearIndex}
              onRebuild={rebuildIndex}
            />
          </div>

          <footer className="page-footer"><span>SPYMONK ENCYCLOPEDIA <i>·</i> PERSONAL KNOWLEDGE SYSTEM</span><span>BUILT FOR TRACEABLE THINKING <Icon name="spark" size={14} /></span></footer>
        </div>
      </main>
    </div>
  )
}

export default App
