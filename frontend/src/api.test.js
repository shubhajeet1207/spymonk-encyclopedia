import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'

import { api, ApiAuthenticationError, clearApiToken, filenameFrom, followResearchJob, getApiToken, parseEventFrame, setApiToken } from './api.js'

const originalFetch = globalThis.fetch
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')

function sse(text) {
  return new Response(text, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

beforeEach(() => {
  const values = new Map()
  Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true,
    value: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: (key) => values.delete(key),
    },
  })
})

afterEach(() => {
  globalThis.fetch = originalFetch
  if (originalStorage) Object.defineProperty(globalThis, 'sessionStorage', originalStorage)
  else delete globalThis.sessionStorage
})

test('all protected request types, including research jobs and their events, send only a Bearer header', async () => {
  const calls = []
  setApiToken('  secret-session-token  ')
  assert.equal(getApiToken(), 'secret-session-token')
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (url.endsWith('/api/research/jobs')) return json({ job_id: 'job-1', status: 'queued' }, 202)
    if (url.includes('/api/research/jobs/job-1/events')) {
      return sse('id: 0\nevent: result\ndata: {"cached":true,"answer":"Saved"}\n\n')
    }
    return json({})
  }

  await api.status()
  await api.topics()
  await api.planSearch({ topic: 'A topic' })
  await api.ask({ question: 'A question?', reverify: true, researchOnMiss: false })
  await api.addDocuments([new Blob(['%PDF-test'], { type: 'application/pdf' })])
  await api.clearMemory()
  await api.rebuildIndex()
  const researched = await api.research({ topic: 'A topic', refresh: false, reverify: true })

  assert.equal(researched.answer, 'Saved')
  assert.equal(calls.length, 9)
  for (const { url, options } of calls) {
    assert.equal(new Headers(options.headers).get('Authorization'), 'Bearer secret-session-token')
    assert.equal(url.includes('secret-session-token'), false)
    assert.equal(String(options.body || '').includes('secret-session-token'), false)
  }
  clearApiToken()
  assert.equal(getApiToken(), '')
  await api.status()
  assert.equal(new Headers(calls.at(-1).options.headers).has('Authorization'), false)
})

test('401 is a distinct, sanitized authentication error for JSON and research requests', async () => {
  setApiToken('private-value')
  globalThis.fetch = async () => json({ detail: 'private-value rejected' }, 401)

  for (const action of [() => api.status(), () => api.research({ topic: 'A topic' })]) {
    await assert.rejects(action, (error) => {
      assert.ok(error instanceof ApiAuthenticationError)
      assert.equal(error.status, 401)
      assert.equal(error.message.includes('private-value'), false)
      return true
    })
  }
})

test('token storage never falls back to persistent browser storage', () => {
  Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true,
    get: () => { throw new Error('blocked') },
  })
  assert.equal(getApiToken(), '')
  assert.throws(() => setApiToken('a-token'), /Session storage is unavailable/)
  assert.doesNotThrow(() => clearApiToken())
})

test('event frames carry their id so a dropped stream can resume', () => {
  assert.deepEqual(parseEventFrame('id: 7\nevent: pipeline\ndata: {"step":"search"}'), { event: 'pipeline', id: 7, data: { step: 'search' } })
  assert.equal(parseEventFrame(': keep-alive'), null)
  assert.equal(parseEventFrame('event: pipeline\ndata: not json'), null)
})

test('a dropped event stream reconnects after the last received event', async () => {
  const urls = []
  const progress = []
  globalThis.fetch = async (url) => {
    urls.push(url)
    if (urls.length === 1) return sse('id: 0\nevent: pipeline\ndata: {"step":"search"}\n\n')
    return sse('id: 1\nevent: result\ndata: {"cached":false}\n\n')
  }
  // Keep the reconnect delay out of the test runtime.
  const originalSetTimeout = globalThis.setTimeout
  globalThis.setTimeout = (callback) => originalSetTimeout(callback, 0)
  try {
    const result = await followResearchJob('job-9', { onProgress: (event) => progress.push(event.step) })
    assert.deepEqual(result, { cached: false })
  } finally {
    globalThis.setTimeout = originalSetTimeout
  }
  assert.deepEqual(progress, ['search'])
  assert.match(urls[0], /after=0$/)
  assert.match(urls[1], /after=1$/)
})

test('a server-reported job error is final and not retried', async () => {
  let calls = 0
  globalThis.fetch = async () => {
    calls += 1
    return sse('id: 4\nevent: error\ndata: {"message":"Public web search is temporarily unavailable (request ID: abc)","status":503}\n\n')
  }
  await assert.rejects(followResearchJob('job-2'), /temporarily unavailable/)
  assert.equal(calls, 1)
})

test('cancelling research cancels the server job', async () => {
  const methods = []
  const controller = new AbortController()
  globalThis.fetch = async (url, options) => {
    methods.push(`${options.method || 'GET'} ${new URL(url, 'http://local').pathname}`)
    if (url.endsWith('/api/research/jobs')) return json({ job_id: 'job-3' }, 202)
    if (url.includes('/events')) {
      controller.abort()
      throw new DOMException('aborted', 'AbortError')
    }
    return json({ job_id: 'job-3', status: 'cancelled' })
  }
  await assert.rejects(api.research({ topic: 'A topic', signal: controller.signal }), /cancelled/)
  assert.deepEqual(methods, ['POST /api/research/jobs', 'GET /api/research/jobs/job-3/events', 'DELETE /api/research/jobs/job-3'])
})

test('download names come from Content-Disposition, with a fallback', () => {
  assert.equal(filenameFrom('attachment; filename="spymonk-report-topic-20261019.pdf"', 'x.pdf'), 'spymonk-report-topic-20261019.pdf')
  assert.equal(filenameFrom(null, 'spymonk-report.pdf'), 'spymonk-report.pdf')
  assert.equal(filenameFrom('attachment', 'fallback.json'), 'fallback.json')
})
