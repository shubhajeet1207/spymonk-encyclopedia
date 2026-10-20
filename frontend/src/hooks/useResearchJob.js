import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../api.js'
import { applyPipelineEvent, failCurrentStep, initialPipeline } from '../pipeline.js'

/** One research job at a time: live pipeline, elapsed time, and cancellation. */
export function useResearchJob() {
  const [pipeline, setPipeline] = useState([])
  const [running, setRunning] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [result, setResult] = useState(null)
  const [startedAt, setStartedAt] = useState(null)
  const [elapsed, setElapsed] = useState(0)
  const controller = useRef(null)

  useEffect(() => {
    if (!running || !startedAt) return undefined
    const update = () => setElapsed(Math.floor((Date.now() - startedAt) / 1000))
    update()
    const timer = setInterval(update, 1000)
    return () => clearInterval(timer)
  }, [running, startedAt])

  // Leaving the page cancels the job instead of letting it run unobserved.
  useEffect(() => () => controller.current?.abort(), [])

  const start = useCallback(async (params) => {
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    setPipeline(initialPipeline())
    setResult(null)
    setRunning(true)
    setCancelling(false)
    setStartedAt(Date.now())
    setElapsed(0)
    try {
      const value = await api.research({
        ...params,
        signal: abort.signal,
        onProgress: (event) => setPipeline((current) => applyPipelineEvent(current, event)),
      })
      setResult(value)
      return value
    } catch (error) {
      setPipeline((current) => failCurrentStep(current, error.message))
      throw error
    } finally {
      if (controller.current === abort) controller.current = null
      setRunning(false)
      setCancelling(false)
    }
  }, [])

  const cancel = useCallback(() => {
    if (!controller.current) return
    setCancelling(true)
    controller.current.abort()
  }, [])

  const reset = useCallback(() => {
    controller.current?.abort()
    setPipeline([])
    setResult(null)
    setStartedAt(null)
    setElapsed(0)
  }, [])

  return { pipeline, running, cancelling, result, elapsed, start, cancel, reset }
}
