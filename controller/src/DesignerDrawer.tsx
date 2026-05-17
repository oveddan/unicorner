import { useEffect, useRef, useState } from 'react'
import type {
  AlternativeOption,
  ControllerSpec,
  GenerateMessage,
  PickAlternativeMessage,
} from './types'
import { useSend } from './transport/context'

type ChatTurn =
  | { role: 'user'; content: string }
  | { role: 'assistant'; content: string; spec?: ControllerSpec }
  | { role: 'alternatives'; content: string; alternatives: AlternativeOption[]; pickedId?: string }

type Props = {
  scene: string
  status: 'idle' | 'thinking' | 'error'
  errorMsg: string | null
  lastSpec: ControllerSpec | null
  alternatives: AlternativeOption[] | null
  /** When a spec arrives, append a synthetic assistant turn to the chat log. */
  onSpecRendered: (turn: ChatTurn) => void
  /** Clears the pending-alternatives state at the app level once we've consumed it. */
  onAlternativesConsumed: () => void
}

function chatStorageKey(scene: string) {
  return `unicorner.chat.${scene || 'default'}`
}

function loadChat(scene: string): ChatTurn[] {
  try {
    const raw = localStorage.getItem(chatStorageKey(scene))
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function saveChat(scene: string, turns: ChatTurn[]) {
  try {
    localStorage.setItem(chatStorageKey(scene), JSON.stringify(turns))
  } catch {
    /* localStorage full or disabled — degrade silently */
  }
}

export function DesignerDrawer({
  scene, status, errorMsg, lastSpec, alternatives,
  onSpecRendered, onAlternativesConsumed,
}: Props) {
  const [open, setOpen] = useState(false)
  const [chat, setChat] = useState<ChatTurn[]>(() => loadChat(scene))
  const [draft, setDraft] = useState('')
  const send = useSend()
  const lastAppliedRef = useRef<ControllerSpec | null>(null)

  // Reload chat history when scene changes — each scene has its own log.
  useEffect(() => {
    setChat(loadChat(scene))
    lastAppliedRef.current = null
  }, [scene])

  // When a new spec arrives that differs from the last one we logged, append
  // an assistant turn. Uses JSON.stringify identity — cheap, specs are small.
  useEffect(() => {
    if (!lastSpec) return
    if (lastAppliedRef.current && JSON.stringify(lastAppliedRef.current) === JSON.stringify(lastSpec)) return
    lastAppliedRef.current = lastSpec
    const rationale = lastSpec.rationale || `Applied a ${lastSpec.controls.length}-control surface.`
    const turn: ChatTurn = { role: 'assistant', content: rationale, spec: lastSpec }
    setChat((prev) => {
      // If the most recent turn is an alternatives chooser, mark its pickedId
      // (best-effort by matching label — we don't know the id at this point).
      // Don't try to be clever; just append the assistant turn.
      const next = [...prev, turn]
      saveChat(scene, next)
      return next
    })
    onSpecRendered(turn)
  }, [lastSpec, scene, onSpecRendered])

  // When alternatives arrive, append a chooser turn and open the drawer so
  // the DJ sees the options.
  useEffect(() => {
    if (!alternatives || alternatives.length === 0) return
    const turn: ChatTurn = {
      role:    'alternatives',
      content: `Pick one of ${alternatives.length} options:`,
      alternatives,
    }
    setChat((prev) => {
      const next = [...prev, turn]
      saveChat(scene, next)
      return next
    })
    setOpen(true)
    onAlternativesConsumed()
  }, [alternatives, scene, onAlternativesConsumed])

  function pickAlternative(alt: AlternativeOption) {
    const msg: PickAlternativeMessage = { type: 'pick_alternative', scene, alt_id: alt.id }
    send(msg)
    // Mark the chooser turn as picked so the chip UI can dim non-chosen options.
    setChat((prev) => {
      const next = prev.map((t, i) => {
        if (i !== prev.length - 1) return t
        if (t.role !== 'alternatives') return t
        return { ...t, pickedId: alt.id }
      })
      saveChat(scene, next)
      return next
    })
  }

  function submit() {
    const prompt = draft.trim()
    if (!prompt) return
    const userTurn: ChatTurn = { role: 'user', content: prompt }
    const nextChat = [...chat, userTurn]
    setChat(nextChat)
    saveChat(scene, nextChat)
    setDraft('')

    const history = nextChat
      .slice(0, -1)
      .filter((t) => t.role === 'user')
      .map((t) => ({ role: 'user' as const, content: t.content }))

    const msg: GenerateMessage = { type: 'generate', prompt, scene, history }
    send(msg)
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  function clearChat() {
    setChat([])
    saveChat(scene, [])
  }

  return (
    <>
      <button
        className="gear"
        title="Designer panel"
        aria-label="Open designer panel"
        onClick={() => setOpen((v) => !v)}
      >
        ⚙
      </button>
      <aside className={`drawer ${open ? 'drawer-open' : ''}`} aria-hidden={!open}>
        <header className="drawer-header">
          <strong>Designer</strong>
          <span className="drawer-scene">scene: {scene || '—'}</span>
          <button className="drawer-close" onClick={() => setOpen(false)} aria-label="Close">×</button>
        </header>

        <div className="drawer-log">
          {chat.length === 0 && (
            <div className="drawer-empty">
              Describe the controller you want — e.g. <em>"a DJ controller with reverb, brightness, and speed"</em>.
            </div>
          )}
          {chat.map((turn, i) => (
            <div key={i} className={`drawer-turn drawer-turn-${turn.role}`}>
              <div className="drawer-turn-role">{turn.role}</div>
              <div className="drawer-turn-content">{turn.content}</div>
              {turn.role === 'alternatives' && (
                <div className="drawer-alternatives">
                  {turn.alternatives.map((alt) => {
                    const picked  = turn.pickedId === alt.id
                    const dimmed  = turn.pickedId !== undefined && !picked
                    const locked  = turn.pickedId !== undefined
                    return (
                      <button
                        key={alt.id}
                        className={`drawer-alt-chip ${picked ? 'drawer-alt-picked' : ''} ${dimmed ? 'drawer-alt-dimmed' : ''}`}
                        onClick={() => !locked && pickAlternative(alt)}
                        disabled={locked}
                        title={alt.description}
                      >
                        <span className="drawer-alt-label">{alt.label}</span>
                        {alt.description && (
                          <span className="drawer-alt-desc">{alt.description}</span>
                        )}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          ))}
          {status === 'thinking' && (
            <div className="drawer-turn drawer-turn-assistant drawer-thinking">
              <div className="drawer-turn-role">assistant</div>
              <div className="drawer-turn-content">regenerating…</div>
            </div>
          )}
          {status === 'error' && errorMsg && (
            <div className="drawer-turn drawer-error">
              <div className="drawer-turn-role">error</div>
              <div className="drawer-turn-content">{errorMsg}</div>
            </div>
          )}
        </div>

        <div className="drawer-input">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="What should this controller do?"
            rows={3}
            disabled={status === 'thinking'}
          />
          <div className="drawer-input-row">
            <button onClick={submit} disabled={status === 'thinking' || !draft.trim()}>
              Send
            </button>
            <button onClick={clearChat} className="drawer-clear" disabled={chat.length === 0}>
              Clear history
            </button>
          </div>
        </div>
      </aside>
    </>
  )
}
