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
  const [offlineRound, setOfflineRound] = useState(0)
  const [retryIn, setRetryIn] = useState(0)
  const epoch = useRef(0)
  const deniedHandler = useRef(onAccessDenied)
  deniedHandler.current = onAccessDenied

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
    setOfflineRound(0)
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
        setAccessState('offline')
        setOfflineRound((round) => round + 1)
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
    load()
  }, [load])

  useEffect(() => {
    if (accessState !== 'offline' || offlineRound === 0) {
      setRetryIn(0)
      return undefined
    }
    const delay = backoffDelay(offlineRound, { base: 2000, max: 60000 })
    const due = Date.now() + delay
    setRetryIn(Math.ceil(delay / 1000))
    const countdown = setInterval(() => setRetryIn(Math.max(0, Math.ceil((due - Date.now()) / 1000))), 1000)
    const timer = setTimeout(load, delay)
    return () => {
      clearTimeout(timer)
      clearInterval(countdown)
    }
  }, [accessState, offlineRound, load])

  return { status, topics, inquiries, accessState, setAccessState, retryIn, refresh, load, reset }
}
