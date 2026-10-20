const PATHS = {
  compass: <><circle cx="12" cy="12" r="9" /><path d="m15.8 8.2-2.6 5-5 2.6 2.6-5 5-2.6Z" /><circle cx="12" cy="12" r="1" /></>,
  search: <><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 4.2 4.2" /></>,
  book: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5v-16Z" /><path d="M4 18.5A2.5 2.5 0 0 1 6.5 16H20M8 7h8M8 10h6" /></>,
  upload: <><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" /><path d="M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5" /></>,
  arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
  spark: <><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z" /><path d="m19 16 .8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z" /></>,
  check: <><path d="m5 12 4 4L19 6" /></>,
  alert: <><path d="M12 3 2.8 19h18.4L12 3Z" /><path d="M12 9v4m0 3h.01" /></>,
  trash: <><path d="M4 7h16M10 11v6m4-6v6M6 7l1 14h10l1-14M9 7V4h6v3" /></>,
  close: <><path d="m6 6 12 12M18 6 6 18" /></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14.7-4.4L4 8" /><path d="M4 4v4h4" /><path d="M4 13a8 8 0 0 0 14.7 4.4L20 16" /><path d="M20 20v-4h-4" /></>,
  download: <><path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5" /><path d="M5 19h14" /></>,
  code: <><path d="m8 8-4 4 4 4M16 8l4 4-4 4" /></>,
}

export function Icon({ name, size = 18 }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {PATHS[name] || PATHS.compass}
    </svg>
  )
}
