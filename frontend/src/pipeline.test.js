import assert from 'node:assert/strict'
import { test } from 'node:test'

import { applyPipelineEvent, elapsedLabel, failCurrentStep, initialPipeline, stageDuration } from './pipeline.js'
import { backoffDelay, sleep } from './retry.js'

test('stage events update only their own step', () => {
  const steps = applyPipelineEvent(initialPipeline(), { step: 'search', status: 'active', message: 'Searching', current: 1, total: 3 })
  assert.equal(steps.find((step) => step.id === 'search').status, 'active')
  assert.equal(steps.find((step) => step.id === 'memory').status, 'pending')
})

test('a failure marks the running stage, or the next pending one', () => {
  let steps = applyPipelineEvent(initialPipeline(), { step: 'memory', status: 'complete', message: 'ok' })
  steps = applyPipelineEvent(steps, { step: 'retrieve', status: 'active', message: 'busy' })
  const failed = failCurrentStep(steps, 'boom')
  assert.equal(failed.find((step) => step.id === 'retrieve').status, 'failed')
  assert.equal(failed.find((step) => step.id === 'retrieve').message, 'boom')
})

test('a stage the server reported as failed gets the error; later stages stay pending', () => {
  let steps = initialPipeline()
  for (const step of ['memory', 'retrieve', 'reverify']) {
    steps = applyPipelineEvent(steps, { step, status: 'complete', message: 'ok' })
  }
  steps = applyPipelineEvent(steps, { step: 'compose', status: 'failed', message: 'This stage could not be completed.' })
  const failed = failCurrentStep(steps, 'The language model did not respond within 60 seconds.')

  assert.deepEqual(failed.filter((step) => step.status === 'failed').map((step) => step.id), ['compose'])
  assert.equal(failed.find((step) => step.id === 'compose').message, 'The language model did not respond within 60 seconds.')
  assert.equal(failed.find((step) => step.id === 'plan').status, 'pending')
})

test('durations and elapsed time are formatted for people', () => {
  assert.equal(stageDuration(250), ' · 250 ms')
  assert.equal(stageDuration(2500), ' · 2.5 s')
  assert.equal(stageDuration(undefined), '')
  assert.equal(elapsedLabel(42), '42s')
  assert.equal(elapsedLabel(125), '2m 5s')
})

test('backoff doubles up to its ceiling', () => {
  assert.deepEqual([1, 2, 3, 4].map((attempt) => backoffDelay(attempt)), [1000, 2000, 4000, 8000])
  assert.equal(backoffDelay(20, { max: 30000 }), 30000)
})

test('sleep stops early when aborted', async () => {
  const controller = new AbortController()
  const waiting = sleep(60000, controller.signal)
  controller.abort()
  await assert.rejects(waiting, { name: 'AbortError' })
})

test('the pipeline ends with answering from new evidence', () => {
  const steps = initialPipeline()
  assert.deepEqual(steps.slice(-2).map((step) => step.id), ['save', 'answer'])
})
