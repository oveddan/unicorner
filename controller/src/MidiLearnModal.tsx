import { useEffect, useRef, useState } from 'react'
import type { ControllerSpec } from './types'
import { useMidi, type MidiEvent } from './midi/MidiProvider'

type Props = {
  spec: ControllerSpec
  onClose: () => void
}

export function MidiLearnModal({ spec, onClose }: Props) {
  const { lastEvent, addMapping } = useMidi()
  const [step, setStep] = useState<'pick' | 'learn'>('pick')
  const [controlId, setControlId] = useState<string | null>(null)
  const [captured, setCaptured] = useState<MidiEvent | null>(null)
  const enteredAtRef = useRef<number>(0)

  useEffect(() => {
    if (step !== 'learn' || !lastEvent) return
    if (lastEvent.time < enteredAtRef.current) return
    setCaptured(lastEvent)
  }, [lastEvent, step])

  function pickControl(id: string) {
    setControlId(id)
    setCaptured(null)
    enteredAtRef.current = performance.now()
    setStep('learn')
  }

  function done() {
    if (!controlId || !captured) return
    addMapping(controlId, {
      device: captured.device,
      channel: captured.channel,
      type: captured.type,
      data1: captured.data1,
    })
    onClose()
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>New MIDI Connection</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        {step === 'pick' && (
          <div className="modal-body">
            <p>Pick a parameter to control:</p>
            <ul className="param-list">
              {spec.controls.map((c) => (
                <li key={c.id}>
                  <button className="param-choice" onClick={() => pickControl(c.id)}>
                    <strong>{c.label}</strong>
                    <span className="param-meta">{c.id} · {c.type}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {step === 'learn' && controlId && (
          <div className="modal-body">
            <p>
              Selected: <strong>{spec.controls.find((c) => c.id === controlId)?.label}</strong>
            </p>
            <p>Now move the MIDI control you want to assign…</p>
            <div className="learn-box">
              {!captured && <div className="learn-waiting">waiting for MIDI…</div>}
              {captured && (
                <div className="learn-captured">
                  <div className="learn-line">
                    <span>device</span><strong>{captured.device}</strong>
                  </div>
                  <div className="learn-line">
                    <span>channel</span><strong>{captured.channel}</strong>
                  </div>
                  <div className="learn-line">
                    <span>type</span><strong>{captured.type}</strong>
                  </div>
                  <div className="learn-line">
                    <span>control #</span><strong>{captured.data1}</strong>
                  </div>
                  <div className="learn-line">
                    <span>live value</span><strong>{captured.value}</strong>
                  </div>
                  <div className="learn-bar">
                    <div className="learn-bar-fill" style={{ width: `${captured.norm * 100}%` }} />
                  </div>
                </div>
              )}
            </div>
            <div className="modal-actions">
              <button onClick={() => setStep('pick')}>back</button>
              <button className="primary" disabled={!captured} onClick={done}>
                Done
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
