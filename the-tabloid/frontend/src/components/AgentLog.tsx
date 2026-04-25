import { useState } from 'react'
import { MSym } from './MSym'
import type { Segment } from '../types'

/**
 * Shows what each agent produced — story brief + research briefing — so the
 * viewer understands HOW the panel ended up debating what they're debating.
 *
 * Renders inline as collapsible cards. Empty when the segment has no
 * `story_brief` or `briefing` yet (e.g., status=queued/debate before research
 * has run).
 */
export function AgentLog({ segment }: { segment: Segment }) {
  const brief = segment.story_brief
  const briefing = segment.briefing
  const script = segment.script

  if (!brief && !briefing && !script) return null

  return (
    <section className="mt-8 space-y-3">
      <div className="flex items-center gap-2 mb-1">
        <MSym name="psychology" filled className="!text-[16px] text-fuchsia-400" />
        <span className="font-mono text-[10px] tracking-[0.25em] uppercase text-fuchsia-400">
          Agent log
        </span>
      </div>

      {brief && (
        <Collapsible
          icon="article"
          title="Why this story"
          subtitle={brief.headline ?? ''}
        >
          <Field label="Headline" value={brief.headline} />
          <Field label="Source" value={brief.source} />
          {brief.url && (
            <a
              href={brief.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 font-mono text-[10px] tracking-wider uppercase text-fuchsia-300 hover:text-fuchsia-200 mt-1"
            >
              open original <MSym name="open_in_new" className="!text-xs" />
            </a>
          )}
          <Bullets label="Key facts" items={brief.key_facts} />
          <Field label="Angle A" value={brief.angle_a} />
          <Field label="Angle B" value={brief.angle_b} />
          <Field label="Why now" value={brief.why_now} />
          {brief.infographic_data?.key_stat && (
            <Field
              label="Key stat"
              value={`${brief.infographic_data.key_stat} — ${brief.infographic_data.stat_source ?? ''}`}
            />
          )}
        </Collapsible>
      )}

      {script?.scenes && script.scenes.length > 0 && (
        <Collapsible
          icon="movie"
          title="Broadcast script"
          subtitle={`${script.scenes.length} scenes · what each shot says`}
        >
          <ol className="space-y-3 mt-2">
            {script.scenes.map((s, i) => (
              <li
                key={i}
                className="border-l-2 border-fuchsia-500/30 pl-3 py-1"
              >
                <div className="flex items-center gap-2 flex-wrap mb-0.5">
                  <span className="font-mono text-[10px] tracking-[0.18em] uppercase text-fuchsia-400">
                    Scene {s.scene_number ?? i + 1}
                  </span>
                  {s.featured_role && (
                    <span className="font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-white/5 text-on-surface-variant">
                      {s.featured_role}
                    </span>
                  )}
                  {s.camera_motion && (
                    <span className="font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-white/5 text-on-surface-variant">
                      {String(s.camera_motion).replace(/_/g, ' ')}
                    </span>
                  )}
                  {s.emotional_beat && (
                    <span className="font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-fuchsia-500/15 text-fuchsia-300">
                      {s.emotional_beat}
                    </span>
                  )}
                </div>
                {s.title && (
                  <div className="font-body text-[12px] text-white/80 mb-0.5">
                    {s.title}
                  </div>
                )}
                {s.vo_line && (
                  <div className="font-body text-[13px] text-white leading-snug italic">
                    "{s.vo_line}"
                  </div>
                )}
              </li>
            ))}
          </ol>
        </Collapsible>
      )}

      {briefing && (
        <Collapsible
          icon="search"
          title="Research briefing"
          subtitle={
            briefing.grounded
              ? 'Gemini · with Google grounding'
              : 'Gemini · pool-grounded'
          }
        >
          {briefing.questions && briefing.questions.length > 0 && (
            <Bullets
              label="Questions the producer chased"
              items={briefing.questions.map((q) =>
                typeof q === 'string' ? q : q.question ?? '',
              )}
            />
          )}
          {briefing.anchor_facts && briefing.anchor_facts.length > 0 && (
            <Bullets
              label="Anchor facts"
              items={briefing.anchor_facts.map((f) =>
                typeof f === 'string'
                  ? f
                  : `${f.claim ?? ''}${f.source ? ` [${f.source}]` : ''}`,
              )}
            />
          )}
          {briefing.counterpoints && briefing.counterpoints.length > 0 && (
            <Bullets label="Counterpoints" items={briefing.counterpoints} />
          )}
          {briefing.pull_quotes && briefing.pull_quotes.length > 0 && (
            <Bullets
              label="Pull quotes"
              items={briefing.pull_quotes.map((q) =>
                typeof q === 'string'
                  ? q
                  : `"${q.quote ?? ''}"${q.attributed_to ? ` — ${q.attributed_to}` : ''}`,
              )}
            />
          )}
          {briefing.fresh_data && briefing.fresh_data.length > 0 && (
            <Bullets
              label="Fresh data"
              items={briefing.fresh_data.map((d) =>
                typeof d === 'string'
                  ? d
                  : `${d.stat ?? ''} ${d.label ?? ''}${d.source ? ` [${d.source}]` : ''}`,
              )}
            />
          )}
        </Collapsible>
      )}
    </section>
  )
}

function Collapsible({
  icon,
  title,
  subtitle,
  children,
}: {
  icon: string
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="glass-card rounded-2xl overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full p-4 flex items-center justify-between gap-3 text-left active:scale-[0.99] transition-transform"
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-9 h-9 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center flex-shrink-0">
            <MSym name={icon} className="!text-[16px] text-white/80" />
          </div>
          <div className="min-w-0">
            <div className="font-mono text-[10px] tracking-[0.18em] uppercase text-on-surface-variant/70">
              {title}
            </div>
            {subtitle && (
              <div className="font-body text-sm text-white truncate">{subtitle}</div>
            )}
          </div>
        </div>
        <MSym
          name="expand_more"
          className={`!text-[18px] text-white/60 transition-transform flex-shrink-0 ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && (
        <div className="px-4 pb-4 pt-1 space-y-2 border-t border-white/5">{children}</div>
      )}
    </div>
  )
}

function Field({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null
  return (
    <div>
      <div className="font-mono text-[9px] tracking-[0.2em] uppercase text-on-surface-variant/60 mt-2">
        {label}
      </div>
      <div className="font-body text-[13px] text-white/90 leading-snug">{value}</div>
    </div>
  )
}

function Bullets({ label, items }: { label: string; items?: (string | undefined)[] }) {
  const cleaned = (items ?? []).filter((s) => s && s.trim().length > 0) as string[]
  if (cleaned.length === 0) return null
  return (
    <div>
      <div className="font-mono text-[9px] tracking-[0.2em] uppercase text-on-surface-variant/60 mt-2">
        {label}
      </div>
      <ul className="space-y-1 mt-1">
        {cleaned.map((s, i) => (
          <li key={i} className="font-body text-[13px] text-white/90 leading-snug flex gap-2">
            <span className="text-fuchsia-400/70 flex-shrink-0">·</span>
            <span>{s}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
