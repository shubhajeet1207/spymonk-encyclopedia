import { AbortedError, backoffDelay, sleep } from './retry.js'

const API_BASE_URL = (import.meta.env?.VITE_API_BASE_URL || '').replace(/\/$/, '')
const TOKEN_KEY = 'spymonk.apiToken'
const MAX_STREAM_RECONNECTS = 5

export class ApiAuthenticationError extends Error {
  constructor() {
    super('API access was denied. Enter a valid personal token to continue.')
    this.name = 'ApiAuthenticationError'
    this.status = 401
  }
}

export class ApiUnreachableError extends Error {
  constructor(id) {
    super(`Cannot reach the SpyMonk API. Check that the backend is running and its URL is configured. (request ID: ${id})`)
    this.name = 'ApiUnreachableError'
  }
}

export function getApiToken() {
  try {
    return globalThis.sessionStorage?.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setApiToken(value) {
  const token = String(value || '').trim()
  if (!token || /\s/.test(token)) {
    throw new Error('Enter one API token without spaces or line breaks.')
  }
  try {
    const storage = globalThis.sessionStorage
    if (!storage) throw new Error('Session storage unavailable')
    storage.setItem(TOKEN_KEY, token)
  } catch {
    throw new Error('Session storage is unavailable in this browser; the API token cannot be saved.')
  }
}

export function clearApiToken() {
  try {
    globalThis.sessionStorage?.removeItem(TOKEN_KEY)
  } catch {
    // A blocked storage API must not leave private workspace data visible.
  }
}

function requestId() {
  return globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function requestHeaders(initial, id) {
  const headers = new Headers(initial || {})
  headers.set('X-Request-ID', id)
  const token = getApiToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return headers
}

function responseError(response, payload, id) {
  if (response.status === 401) return new ApiAuthenticationError()
  const message = typeof payload.detail === 'string'
    ? payload.detail
    : `The request failed (${response.status}).`
  const responseRequestId = response.headers.get('x-request-id') || id
  const requestReference = responseRequestId && !message.includes(responseRequestId)
    ? ` (request ID: ${responseRequestId})`
    : ''
  const error = new Error(`${message}${requestReference}`)
  error.status = response.status
  return error
}

async function send(path, options, id) {
  try {
    return await fetch(`${API_BASE_URL}${path}`, { ...options, headers: requestHeaders(options.headers, id) })
  } catch {
    if (options.signal?.aborted) throw new AbortedError()
    throw new ApiUnreachableError(id)
  }
}

async function request(path, options = {}) {
  const id = requestId()
  const response = await send(path, options, id)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw responseError(response, payload, id)
  return payload
}

/** The file name from a Content-Disposition header, or the fallback. */
export function filenameFrom(header, fallback) {
  const match = /filename="([^"]+)"/.exec(header || '')
  return match ? match[1] : fallback
}

/**
 * Fetch an attachment with the API token and hand it to the browser as a
 * download; a plain link could not send the Authorization header.
 */
async function download(path, fallbackName) {
  const id = requestId()
  const response = await send(path, {}, id)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw responseError(response, payload, id)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filenameFrom(response.headers.get('content-disposition'), fallbackName)
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Parse one server-sent-event frame into {event, id, data}, or null. */
export function parseEventFrame(frame) {
  let event = 'message'
  let id = null
  const data = []
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
    else if (line.startsWith('id:')) id = Number(line.slice(3).trim())
  }
  if (!data.length) return null
  try {
    return { event, id: Number.isFinite(id) ? id : null, data: JSON.parse(data.join('\n')) }
  } catch {
    return null
  }
}

/** Yield parsed events from a text/event-stream response body. */
export async function* readEvents(response) {
  if (!response.body) throw new Error('The server did not provide a research progress stream.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() || ''
      for (const frame of frames) {
        const message = parseEventFrame(frame)
        if (message) yield message
      }
      if (done) break
    }
    const last = buffer.trim() ? parseEventFrame(buffer) : null
    if (last) yield last
  } finally {
    reader.releaseLock()
  }
}

/**
 * Follow a research job's events until its result. A dropped connection
 * resumes from the last received event instead of restarting the research.
 */
export async function followResearchJob(jobId, { onProgress, signal } = {}) {
  let after = 0
  let failures = 0
  while (true) {
    const id = requestId()
    let response = null
    try {
      response = await send(
        `/api/research/jobs/${encodeURIComponent(jobId)}/events?after=${after}`,
        { headers: { Accept: 'text/event-stream' }, signal },
        id,
      )
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}))
        throw responseError(response, payload, id)
      }
      for await (const message of readEvents(response)) {
        failures = 0
        if (message.id !== null) after = message.id + 1
        if (message.event === 'pipeline') onProgress?.(message.data)
        else if (message.event === 'result') return message.data
        else if (message.event === 'error') throw Object.assign(new Error(message.data.message || 'Research could not be completed.'), { status: message.data.status, final: true })
      }
    } catch (error) {
      if (signal?.aborted) throw new AbortedError()
      // Server-reported failures are final; only transport problems are retried.
      if (error.final || error instanceof ApiAuthenticationError || (response && !response.ok)) throw error
    }
    failures += 1
    if (failures > MAX_STREAM_RECONNECTS) {
      throw new Error('Lost connection to the research job. It may still be running; refresh the page to check saved topics.')
    }
    await sleep(backoffDelay(failures), signal)
  }
}

async function research({ topic, refresh, reverify, searchQueries = [], onProgress, onJob, signal }) {
  const job = await request('/api/research/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topic, refresh, reverify, search_queries: searchQueries }),
    signal,
  })
  onJob?.(job.job_id)
  try {
    return await followResearchJob(job.job_id, { onProgress, signal })
  } catch (error) {
    if (signal?.aborted) {
      // Stop spending on research nobody is waiting for.
      await request(`/api/research/jobs/${encodeURIComponent(job.job_id)}`, { method: 'DELETE' }).catch(() => {})
      throw new AbortedError('Research was cancelled; nothing was saved.')
    }
    throw error
  }
}

export const api = {
  getToken: getApiToken,
  setToken: setApiToken,
  clearToken: clearApiToken,
  status: () => request('/api/status'),
  topics: () => request('/api/topics'),
  planSearch: ({ topic }) => request('/api/research/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topic }),
  }),
  research,
  ask: ({ question, reverify, researchOnMiss }) => request('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, reverify, research_on_miss: researchOnMiss }),
  }),
  addDocuments: (files) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    return request('/api/documents', { method: 'POST', body: form })
  },
  clearMemory: () => request('/api/memory', { method: 'DELETE' }),
  rebuildIndex: () => request('/api/memory/reindex', { method: 'POST' }),
  inquiries: () => request('/api/inquiries'),
  downloadReport: (id) => download(`/api/inquiries/${encodeURIComponent(id)}/report.pdf`, 'spymonk-report.pdf'),
  downloadDeveloperLog: (id) => download(`/api/inquiries/${encodeURIComponent(id)}/developer-log`, 'spymonk-developer-log.json'),
}
