import { Icon } from './Icon.jsx'

export function Notice({ notice, onDismiss }) {
  if (!notice) return null
  const isError = notice.kind === 'error'
  return (
    <div className={`notice ${isError ? 'notice-error' : 'notice-success'}`} role={isError ? 'alert' : 'status'}>
      <Icon name={isError ? 'alert' : 'check'} />
      <p>{notice.message}</p>
      <button className="icon-button" type="button" onClick={onDismiss} aria-label="Dismiss message"><Icon name="close" size={16} /></button>
    </div>
  )
}
