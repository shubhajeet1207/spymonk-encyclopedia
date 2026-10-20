import assert from 'node:assert/strict'
import { test } from 'node:test'

import { researchNotice, UNVERIFIED_MESSAGE, unverifiedOutcome } from './outcome.js'

test('a run that verified nothing shows the unverified message and offers the report', () => {
  const notice = researchNotice({
    archive: { memory_updated: false, failure_reason: 'no_independent_support', inquiry_id: '20261019120000abcdefabcdefabcd' },
  })
  assert.equal(notice.kind, 'error')
  assert.ok(notice.message.startsWith(UNVERIFIED_MESSAGE))
  assert.match(notice.message, /report explaining why/)
})

test('a run that verified nothing keeps earlier answers available', () => {
  const notice = researchNotice({ archive: { memory_updated: false, previous_verified_claims: 3 } })
  assert.match(notice.message, /earlier verified answers for this topic are unchanged/)
})

test('unverifiedOutcome is null for saved runs and describes unverified ones', () => {
  assert.equal(unverifiedOutcome({ memory_updated: true }), null)
  assert.equal(unverifiedOutcome(null), null)
  const outcome = unverifiedOutcome({ memory_updated: false, outcome_explanation: 'Only one site.', inquiry_id: 'abc' })
  assert.deepEqual(outcome, { message: UNVERIFIED_MESSAGE, explanation: 'Only one site.', inquiryId: 'abc', hasEarlierAnswers: false })
})

test('a partial save reports stored, held-back, and kept claims', () => {
  const notice = researchNotice({
    archive: {
      memory_updated: true,
      claims: [{}, {}, {}, {}],
      unresolved_claims: [{}, {}],
      held_back_claims: 2,
      carried_over_claims: 1,
    },
  })
  assert.equal(notice.kind, 'success')
  assert.match(notice.message, /3 corroborated claims stored/)
  assert.match(notice.message, /2 of those were held back/)
  assert.match(notice.message, /1 previously verified claims were kept/)
})

test('a saved run with an answer points to it', () => {
  const notice = researchNotice({ answer: 'Use docker run.', archive: { memory_updated: true, claims: [{}], unresolved_claims: [] } })
  assert.match(notice.message, /The answer is shown below/)
})
