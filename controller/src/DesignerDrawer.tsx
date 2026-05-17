import { useEffect, useRef, useState } from 'react'
import type {
  AlternativeOption,
  ControllerSpec,
  GenerateMessage,
  PickAlternativeMessage,
  SceneSummary,
  UnderstandSceneMessage,
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
  sceneSummary: SceneSummary | null
  summaryStatus: 'idle' | 'thinking' | 'error'
  /** When a spec arrives, append a synthetic assistant turn to the chat log. */
  onSpecRendered: (turn: ChatTurn) => void
  /** Clears the pending-alternatives state at the app level once we've consumed it. */
  onAlternativesConsumed: () => void
  /** Persist a user edit (or clear) to the scene summary. */
  onSceneSummaryChange: (next: SceneSummary | null) => void
}

function chatStorageKey(scene: string) {
  return `unicorner.chat.${scene || 'default'}`
}

function draftStorageKey(scene: string) {
  return `unicorner.chat.draft.${scene || 'default'}`
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

function loadDraft(scene: string): string {
  try {
    return localStorage.getItem(draftStorageKey(scene)) || ''
  } catch {
    return ''
  }
}

function saveDraft(scene: string, text: string) {
  try {
    if (text) localStorage.setItem(draftStorageKey(scene), text)
    else localStorage.removeItem(draftStorageKey(scene))
  } catch {
    /* degrade silently */
  }
}

export function DesignerDrawer({
  scene, status, errorMsg, lastSpec, alternatives,
  sceneSummary, summaryStatus,
  onSpecRendered, onAlternativesConsumed, onSceneSummaryChange,
}: Props) {
  const [open, setOpen] = useState(false)
  const [chat, setChat] = useState<ChatTurn[]>(() => loadChat(scene))
  const [draft, setDraft] = useState(() => loadDraft(scene))
  const [summaryEditing, setSummaryEditing] = useState(false)
  const [summaryDraft, setSummaryDraft] = useState('')
  const send = useSend()
  const lastAppliedRef = useRef<ControllerSpec | null>(null)
  const logRef = useRef<HTMLDivElement | null>(null)
  // Track whether the user has scrolled away from the bottom; if so, we
  // don't yank them back when a new turn arrives.
  const autoScrollRef = useRef(true)

  // Reload chat history + draft when scene changes — each scene has its own.
  useEffect(() => {
    setChat(loadChat(scene))
    setDraft(loadDraft(scene))
    setSummaryEditing(false)
    lastAppliedRef.current = null
  }, [scene])

  // Persist draft text per scene so closing the drawer mid-prompt doesn't
  // lose work.
  useEffect(() => {
    saveDraft(scene, draft)
  }, [scene, draft])

  // Auto-scroll the chat log to the bottom on new turns, *unless* the user
  // has scrolled up to read older content.
  useEffect(() => {
    const el = logRef.current
    if (!el) return
    if (!autoScrollRef.current) return
    el.scrollTop = el.scrollHeight
  }, [chat.length, status, summaryStatus, sceneSummary])

  function onLogScroll() {
    const el = logRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    autoScrollRef.current = nearBottom
  }

  // When a new spec arrives that differs from the last one we logged, append
  // an assistant turn. Uses JSON.stringify identity — cheap, specs are small.
  useEffect(() => {
    if (!lastSpec) return
    if (lastAppliedRef.current && JSON.stringify(lastAppliedRef.current) === JSON.stringify(lastSpec)) return
    lastAppliedRef.current = lastSpec
    const rationale = lastSpec.rationale || `Applied a ${lastSpec.controls.length}-control surface.`
    const turn: ChatTurn = { role: 'assistant', content: rationale, spec: lastSpec }
    setChat((prev) => {
      const next = [...prev, turn]
      saveChat(scene, next)
      return next
    })
    onSpecRendered(turn)
  }, [lastSpec, scene, onSpecRendered])

  // When alternatives arrive, append a chooser turn and open the drawer so
  // the DJ sees the options. Works for both choice-kind and question-kind
  // (the chip UI is identical; server-side handles the difference on tap).
  useEffect(() => {
    if (!alternatives || alternatives.length === 0) return
    const isQuestion = alternatives.some((a) => a.kind === 'question')
    const turn: ChatTurn = {
      role:    'alternatives',
      content: isQuestion
        ? 'I need a bit more to go on — pick the answer that fits best:'
        : `Pick one of ${alternatives.length} options:`,
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
    // If this was an answer to a clarifying question, also append the user's
    // chosen answer as a user turn so the chat log reads like a conversation.
    if (alt.kind === 'question') {
      setChat((prev) => {
        const next: ChatTurn[] = [...prev, { role: 'user', content: alt.label }]
        saveChat(scene, next)
        return next
      })
    }
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

    const msg: GenerateMessage = {
      type:    'generate',
      prompt,
      scene,
      history,
      ...(sceneSummary && sceneSummary.text ? { scene_summary: sceneSummary.text } : {}),
    }
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

  function rescanScene() {
    const msg: UnderstandSceneMessage = { type: 'understand_scene', scene }
    send(msg)
  }

  function startEditSummary() {
    if (!sceneSummary) return
    setSummaryDraft(sceneSummary.text)
    setSummaryEditing(true)
  }

  function saveEditedSummary() {
    const text = summaryDraft.trim()
    if (!text) {
      setSummaryEditing(false)
      return
    }
    onSceneSummaryChange({
      text,
      generatedAt:  sceneSummary?.generatedAt ?? Date.now(),
      stale:        false,
      editedByUser: true,
    })
    setSummaryEditing(false)
  }

  function cancelEditSummary() {
    setSummaryEditing(false)
  }

  function clearSummary() {
    onSceneSummaryChange(null)
    setSummaryEditing(false)
  }

  const summaryBusy = summaryStatus === 'thinking'

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
          <button
            className="drawer-rescan"
            onClick={rescanScene}
            disabled={summaryBusy}
            title={sceneSummary ? 'Re-scan and refresh the scene summary' : 'Scan the scene so the AI knows what it is'}
          >
            {summaryBusy
              ? 'Scanning…'
              : sceneSummary
                ? '↻ Re-scan'
                : '🔍 Scan scene'}
          </button>
          <button className="drawer-close" onClick={() => setOpen(false)} aria-label="Close">×</button>
        </header>

        {sceneSummary && (
          <section className={`drawer-summary ${sceneSummary.stale ? 'drawer-summary-stale' : ''}`}>
            <div className="drawer-summary-head">
              <span className="drawer-summary-title">
                Scene understanding
                {sceneSummary.editedByUser && <span className="drawer-summary-tag">edited</span>}
                {sceneSummary.stale && <span className="drawer-summary-tag drawer-summary-tag-stale">stale</span>}
              </span>
              {!summaryEditing && (
                <div className="drawer-summary-actions">
                  <button onClick={startEditSummary} className="drawer-summary-btn" title="Correct the summary">
                    Edit
                  </button>
                  <button onClick={clearSummary} className="drawer-summary-btn drawer-summary-btn-quiet" title="Clear the summary">
                    Clear
                  </button>
                </div>
              )}
            </div>
            {sceneSummary.stale && !summaryEditing && (
              <div className="drawer-summary-banner">
                Scene changed since this summary was written — click <strong>↻ Re-scan</strong> to refresh.
              </div>
            )}
            {summaryEditing ? (
              <>
                <textarea
                  className="drawer-summary-editor"
                  value={summaryDraft}
                  onChange={(e) => setSummaryDraft(e.target.value)}
                  rows={6}
                  autoFocus
                />
                <div className="drawer-summary-edit-actions">
                  <button onClick={saveEditedSummary} className="drawer-summary-btn drawer-summary-btn-primary">
                    Save
                  </button>
                  <button onClick={cancelEditSummary} className="drawer-summary-btn drawer-summary-btn-quiet">
                    Cancel
                  </button>
                </div>
              </>
            ) : (
              <p className="drawer-summary-text">{sceneSummary.text}</p>
            )}
          </section>
        )}

        {!sceneSummary && !summaryBusy && (
          <div className="drawer-summary-hint">
            Tip: tap <strong>🔍 Scan scene</strong> first so the AI knows what's in your TouchDesigner project before designing a controller.
          </div>
        )}

        <div className="drawer-log" ref={logRef} onScroll={onLogScroll}>
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
          {summaryBusy && (
            <div className="drawer-turn drawer-turn-assistant drawer-thinking">
              <div className="drawer-turn-role">scanning scene</div>
              <div className="drawer-turn-content">reading parameters…</div>
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
