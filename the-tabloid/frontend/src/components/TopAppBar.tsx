import type { ReactNode } from 'react'
import { MSym } from './MSym'

type Status = 'on_air' | 'ready' | 'none'

export function TopAppBar({
  onBack,
  status = 'on_air',
  centerTitle = false,
  right,
}: {
  onBack?: () => void
  status?: Status
  centerTitle?: boolean
  right?: ReactNode
}) {
  const badge =
    status === 'on_air' ? (
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 rounded-full bg-fuchsia-500 animate-pulseDot shadow-[0_0_8px_rgba(217,70,239,0.8)]" />
        <span className="font-mono text-[10px] tracking-[0.18em] text-white">· ON AIR</span>
      </div>
    ) : status === 'ready' ? (
      <span className="px-2 py-0.5 rounded bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 font-mono text-[10px] tracking-wide font-bold uppercase">
        READY
      </span>
    ) : null

  return (
    <header className="fixed top-0 left-0 z-50 w-full h-16 px-4 flex items-center justify-between bg-canvas/80 backdrop-blur-xl border-b border-white/15">
      <div className="flex items-center gap-3 min-w-[96px]">
        {onBack ? (
          <button
            type="button"
            onClick={onBack}
            className="flex items-center gap-1.5 text-white/80 hover:text-white active:scale-95 transition"
            aria-label="Back"
          >
            <MSym name="arrow_back_ios" className="!text-[16px]" />
            <span className="font-mono text-[11px] tracking-[0.18em] uppercase">Back</span>
          </button>
        ) : (
          <h1 className={centerTitle ? 'hidden' : 'font-display font-black italic tracking-tight text-white text-xl uppercase'}>
            the tabloid
          </h1>
        )}
      </div>

      {centerTitle && (
        <h1 className="absolute left-1/2 -translate-x-1/2 font-display font-black italic tracking-tight text-white text-lg md:text-xl uppercase">
          the tabloid
        </h1>
      )}

      <div className="flex items-center gap-3 min-w-[96px] justify-end">
        {right}
        {badge}
      </div>
    </header>
  )
}
