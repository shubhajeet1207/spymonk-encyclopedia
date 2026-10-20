/** Shown when research could not verify any claim for the question. */
export const UNVERIFIED_MESSAGE = 'We cannot verify the claims for providing you the answers of your question.'

/**
 * The unverified outcome of a research archive summary, or null when the run
 * saved verified claims. `hasEarlierAnswers` means saved memory still answers.
 */
export function unverifiedOutcome(archive) {
  if (!archive || archive.memory_updated) return null
  return {
    message: archive.outcome_message || UNVERIFIED_MESSAGE,
    explanation: archive.outcome_explanation || '',
    inquiryId: archive.inquiry_id || null,
    hasEarlierAnswers: (archive.previous_verified_claims || 0) > 0,
  }
}

/** The notice shown after a research run, explaining what was saved and why. */
export function researchNotice(result) {
  if (result.cached) {
    return { kind: 'success', message: 'The archive already has supported evidence for this topic.' }
  }
  const archive = result.archive || {}
  const unverified = unverifiedOutcome(archive)
  if (unverified) {
    if (unverified.hasEarlierAnswers) {
      return {
        kind: 'error',
        message: 'No new claim could be verified, so nothing new was saved. Your earlier verified answers for this topic are unchanged.',
      }
    }
    const report = unverified.inquiryId ? ' A report explaining why is ready to download below.' : ''
    return { kind: 'error', message: `${unverified.message}${report}` }
  }
  const carried = archive.carried_over_claims || 0
  const stored = (archive.claims?.length || 0) - carried
  const unresolved = archive.unresolved_claims?.length || 0
  const heldBack = archive.held_back_claims || 0
  let message = `Research complete: ${stored} corroborated claims stored; ${unresolved} unresolved claims kept for review.`
  if (result.answer) message += ' The answer is shown below.'
  if (heldBack) message += ` ${heldBack} of those were held back because a cross-check could not finish; researching again can verify them.`
  if (carried) message += ` ${carried} previously verified claims were kept.`
  return { kind: 'success', message }
}
