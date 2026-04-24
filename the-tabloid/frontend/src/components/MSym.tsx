import type { CSSProperties } from 'react'

/** Material Symbols Outlined icon (ligature-based). */
export function MSym({
  name,
  filled = false,
  className = '',
  style,
}: {
  name: string
  filled?: boolean
  className?: string
  style?: CSSProperties
}) {
  return (
    <span className={`msym ${filled ? 'msym-fill' : ''} ${className}`} style={style}>
      {name}
    </span>
  )
}
