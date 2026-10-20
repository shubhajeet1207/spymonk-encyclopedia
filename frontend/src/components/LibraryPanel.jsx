import { useRef } from 'react'

import { Icon } from './Icon.jsx'

export function LibraryPanel({ selectedFiles, onFilesChange, onSubmit, apiOnline, busy, status, inputRef }) {
  const fallbackRef = useRef(null)
  const fileInput = inputRef || fallbackRef
  return (
    <section className="panel library-panel" aria-labelledby="library-heading">
      <div className="panel-heading">
        <div className="panel-icon lavender-icon"><Icon name="upload" size={19} /></div>
        <div><div className="eyebrow small-eyebrow">PRIVATE COLLECTION / PDF</div><h2 id="library-heading">Add to your library</h2></div>
        <span className="panel-number">02 / 02</span>
      </div>
      <p className="panel-copy">Import text-based PDFs from your device. Files are stored on the API host and indexed for search. A file that is already indexed is skipped.</p>
      <form onSubmit={onSubmit}>
        <input ref={fileInput} className="visually-hidden" type="file" accept="application/pdf,.pdf" multiple onChange={(event) => onFilesChange(Array.from(event.target.files || []))} aria-label="Choose local PDF files" />
        <button
          className={`drop-zone ${selectedFiles.length ? 'has-files' : ''}`}
          type="button"
          onClick={() => fileInput.current?.click()}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault()
            onFilesChange(Array.from(event.dataTransfer.files || []).filter((file) => file.name.toLowerCase().endsWith('.pdf')))
          }}
        >
          <span className="drop-icon"><Icon name="upload" size={22} /></span>
          <strong>{selectedFiles.length ? `${selectedFiles.length} PDF${selectedFiles.length === 1 ? '' : 's'} selected` : 'Drop files here, or browse'}</strong>
          <span>PDF · up to 10 files · 20 MB per file</span>
        </button>
        {selectedFiles.length > 0 && (
          <ul className="selected-files">
            {selectedFiles.map((file) => (
              <li key={`${file.name}-${file.size}`}><span>{file.name}</span><small>{(file.size / (1024 * 1024)).toFixed(1)} MB</small><button className="text-button" type="button" onClick={() => onFilesChange(selectedFiles.filter((item) => item !== file))}>Remove</button></li>
            ))}
          </ul>
        )}
        <div className="upload-actions">
          <p className="field-hint">Scanned/image-only PDFs need OCR before indexing.</p>
          <button className="button button-primary" type="submit" disabled={!apiOnline || !selectedFiles.length || Boolean(busy)}>{busy === 'upload' ? <><span className="button-spinner" /> Indexing</> : <>Add to memory <Icon name="arrow" size={16} /></>}</button>
        </div>
      </form>
      {status?.local_pdf_count > 0 && (
        <div className="library-summary">
          <Icon name="book" size={17} />
          <div>
            <strong>{status.indexed_pdf_count ?? 0} indexed · {status.local_pdf_count} stored PDF{status.local_pdf_count === 1 ? '' : 's'}</strong>
            <span>Stored PDF copies remain available even if the search index is cleared; rebuilding the index restores them.</span>
          </div>
        </div>
      )}
    </section>
  )
}
