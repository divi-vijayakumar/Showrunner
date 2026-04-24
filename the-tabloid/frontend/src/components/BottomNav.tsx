import { MSym } from './MSym'

/** Decorative bottom nav shell — matches the design mockups. Items are inert
 * placeholders for the hackathon; the active tab is "sensors" on debate and
 * "explore" on other screens. */
export function BottomNav({ active = 'sensors' }: { active?: 'sensors' | 'explore' | 'chat' | 'person' }) {
  const items: Array<{ id: typeof active; icon: string; filled?: boolean }> = [
    { id: 'sensors', icon: 'sensors', filled: true },
    { id: 'explore', icon: 'explore' },
    { id: 'chat', icon: 'chat_bubble' },
    { id: 'person', icon: 'person' },
  ]
  return (
    <nav
      className="fixed bottom-0 left-0 w-full z-40 flex justify-around items-center h-20 px-4 bg-white/5 backdrop-blur-2xl border-t border-white/10 rounded-t-2xl shadow-2xl"
      style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
    >
      {items.map((it) => {
        const isActive = it.id === active
        return (
          <button
            key={it.id}
            type="button"
            className={`flex flex-col items-center gap-1 active:scale-90 transition-transform duration-200 ${
              isActive
                ? 'text-transparent bg-clip-text bg-gradient-to-r from-purple-500 to-pink-500 scale-110'
                : 'text-white/40 hover:text-white/70'
            }`}
          >
            <MSym name={it.icon} filled={isActive ? it.filled : false} />
          </button>
        )
      })}
    </nav>
  )
}
