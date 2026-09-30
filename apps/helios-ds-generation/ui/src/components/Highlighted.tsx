import { ReactNode } from 'react'

export interface Span {
  start: number
  end: number
  kind: 'mention' | 'evidence'
  label: string
  focus?: boolean
}

/**
 * Renders text with ground-truth spans: evidence passages are underlined, entity
 * mentions are highlighted (mentions usually sit inside evidence passages).
 */
export function Highlighted({ text, spans }: { text: string; spans: Span[] }) {
  const valid = spans.filter(s => s.start >= 0 && s.end <= text.length && s.end > s.start)
  const cuts = Array.from(new Set([0, text.length, ...valid.flatMap(s => [s.start, s.end])])).sort(
    (a, b) => a - b
  )
  const parts: ReactNode[] = []
  for (let i = 0; i < cuts.length - 1; i++) {
    const [from, to] = [cuts[i], cuts[i + 1]]
    const active = valid.filter(s => s.start <= from && s.end >= to)
    const piece = text.slice(from, to)
    if (active.length === 0) {
      parts.push(piece)
      continue
    }
    const classes = [
      active.some(s => s.kind === 'evidence') ? 'gt-evidence' : '',
      active.some(s => s.kind === 'mention') ? 'gt-mention' : '',
      active.some(s => s.focus) ? 'gt-focus' : '',
    ]
      .filter(Boolean)
      .join(' ')
    parts.push(
      <span key={from} className={classes} title={active.map(s => s.label).join('\n')}>
        {piece}
      </span>
    )
  }
  return <>{parts}</>
}
