import { useCallback, useEffect, useRef, useState } from 'react'

import { api, ApiAuthenticationError } from '../api.js'
import { backoffDelay } from '../retry.js'

/**
 * Workspace status and saved topics. While the API is unreachable it retries
 * with exponential backoff instead of waiting for a page reload.
 */
export function useWorkspace({ onAccessDenied } = {}) {
  const [status, setStatus] = useState(null)
  const [topics, setTopics] = useState([])
  const [inquiries, setInquiries] = useState([])
  const [accessState, setAccessState] = useState('checking')
  // While offline: which retry this is and when it is due; `now` ticks the countdown.
  const [retry, setRetry] = useState(null)
  const [now, setNow] = useState(0)
  const epoch = useRef(0)
  const deniedHandler = useRef(onAccessDenied)

  useEffect(() => {
    deniedHandler.current = onAccessDenied
  }, [onAccessDenied])

  /** Reload status and topics; throws so callers can report failures. */
  const refresh = useCallback(async () => {
    const current = epoch.current
    const [nextStatus, nextTopics, nextInquiries] = await Promise.all([
      api.status(),
      api.topics(),
      // The list is secondary: an older API without it must not block the workspace.
      api.inquiries().catch((error) => {
        if (error instanceof ApiAuthenticationError) throw error
        return { items: [] }
      }),
    ])
    if (current !== epoch.current) return
    setStatus(nextStatus)
    setTopics(nextTopics.items || [])
    setInquiries(nextInquiries.items || [])
    setAccessState('ready')
    setRetry(null)
  }, [])

  /** Reload, classifying failures; returns the error (or null). */
  const load = useCallback(async () => {
    try {
      await refresh()
      return null
    } catch (error) {
      if (error instanceof ApiAuthenticationError) {
        setAccessState('required')
        deniedHandler.current?.(error)
      } else {
        const failedAt = Date.now()
        setAccessState('offline')
        setNow(failedAt)
        setRetry((current) => {
          const round = (current?.round || 0) + 1
          return { round, due: failedAt + backoffDelay(round, { base: 2000, max: 60000 }) }
        })
      }
      return error
    }
  }, [refresh])

  /** Forget everything loaded under the previous access token. */
  const reset = useCallback(() => {
    epoch.current += 1
    setStatus(null)
    setTopics([])
    setInquiries([])
  }, [])

  useEffect(() => {
    // Fetch on mount: load() sets state only after the request settles.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])

  useEffect(() => {
    if (accessState !== 'offline' || !retry) return undefined
    const countdown = setInterval(() => setNow(Date.now()), 1000)
    const timer = setTimeout(load, Math.max(0, retry.due - Date.now()))
    return () => {
      clearTimeout(timer)
      clearInterval(countdown)
    }
  }, [accessState, retry, load])

  const retryIn = accessState === 'offline' && retry ? Math.max(0, Math.ceil((retry.due - now) / 1000)) : 0

  return { status, topics, inquiries, accessState, setAccessState, retryIn, refresh, load, reset }
}
