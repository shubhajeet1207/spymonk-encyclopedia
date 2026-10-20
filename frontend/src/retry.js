export class AbortedError extends Error {
  constructor(message = 'The request was cancelled.') {
    super(message)
    this.name = 'AbortError'
  }
}

/** Exponential backoff: 1s, 2s, 4s… capped at `max`. */
export function backoffDelay(attempt, { base = 1000, max = 30000 } = {}) {
  return Math.min(max, base * 2 ** Math.max(0, attempt - 1))
}

/** Wait `ms`, or reject early when the signal aborts. */
export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new AbortedError())
      return
    }
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(timer)
      reject(new AbortedError())
    }, { once: true })
  })
}
