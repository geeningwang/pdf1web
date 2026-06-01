import React, { useCallback, useEffect, useRef, useState } from 'react'
import type {
  AgentConfigResponse,
  AgentStep,
  ChatTurn,
} from '../api'
import { fetchAgentConfig, agentUndo } from '../api'

// ------------------------------------------------------------------ step icons

const STEP_ICON: Record<string, string> = {
  thinking:     '⚙',
  llm_request:  '↗',
  llm_response: '↙',
  validation:   '✓',
  relink:       '🔗',
  retry:        '↺',
}

function stepIcon(step: AgentStep): string {
  if (step.type === 'validation') return step.status === 'fail' ? '✗' : '✓'
  return STEP_ICON[step.type] ?? '·'
}

function stepClass(step: AgentStep): string {
  if (step.type === 'validation') return step.status === 'fail' ? 'ai-step-fail' : 'ai-step-ok'
  if (step.type === 'retry') return 'ai-step-retry'
  if (step.type === 'relink') return 'ai-step-ok'
  return 'ai-step-info'
}

// ------------------------------------------------------------------ diff view

function DiffView({ unified }: { unified: string }) {
  if (!unified) return null
  const lines = unified.split('\n')
  return (
    <div className="ai-diff">
      {lines.map((line, i) => {
        let cls = 'ai-diff-ctx'
        if (line.startsWith('+') && !line.startsWith('+++')) cls = 'ai-diff-add'
        else if (line.startsWith('-') && !line.startsWith('---')) cls = 'ai-diff-del'
        else if (line.startsWith('@@')) cls = 'ai-diff-hunk'
        return <div key={i} className={cls}>{line || ' '}</div>
      })}
    </div>
  )
}

function buildAgentCopyText(turn: ChatTurn): string {
  const sections: string[] = []
  const requestResponseSteps = (turn.steps ?? []).filter(
    step => step.type === 'llm_request' || step.type === 'llm_response',
  )

  for (const step of requestResponseSteps) {
    const title = `=== ${step.type === 'llm_request' ? 'REQUEST' : 'RESPONSE'}${step.round != null ? ` R${step.round}` : ''} ===`
    const body = step.detail?.trim() || step.text?.trim()
    if (body) {
      sections.push(`${title}\n\n${body}`)
    }
  }

  return sections.join('\n\n')
}

// ------------------------------------------------------------------ step timeline

interface StepTimelineProps {
  steps: AgentStep[]
  done: boolean
}

const StepTimeline: React.FC<StepTimelineProps> = ({ steps, done }) => {
  const [expanded, setExpanded] = useState(false)
  const [openDetail, setOpenDetail] = useState<number | null>(null)

  // Auto-expand while running, collapse when done
  useEffect(() => {
    if (!done) setExpanded(true)
    else setExpanded(false)
  }, [done])

  const rounds = steps.filter(s => s.type === 'llm_request').length
  const summary = done
    ? `${rounds} LLM round${rounds !== 1 ? 's' : ''} — click to expand`
    : 'Agent working…'

  return (
    <div className="ai-timeline">
      <button
        className="ai-timeline-toggle"
        onClick={() => setExpanded(v => !v)}
      >
        <span className="ai-timeline-icon">{done ? '▶' : '⟳'}</span>
        {summary}
      </button>

      {expanded && (
        <div className="ai-timeline-steps">
          {steps.map((step, i) => (
            <div key={i} className={`ai-step ${stepClass(step)}`}>
              <span className="ai-step-icon">{stepIcon(step)}</span>
              <span className="ai-step-text">
                {step.round != null && <span className="ai-step-round">R{step.round} </span>}
                {step.text}
              </span>
              {step.detail && (
                <button
                  className="ai-step-detail-toggle"
                  onClick={() => setOpenDetail(openDetail === i ? null : i)}
                >
                  {openDetail === i ? '▲' : '▼'}
                </button>
              )}
              {step.detail && openDetail === i && (
                <pre className="ai-step-detail">{step.detail}</pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ chat message

interface TurnViewProps {
  turn: ChatTurn
  onDownload?: () => void
}

const TurnView: React.FC<TurnViewProps> = ({ turn, onDownload }) => {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')

  if (turn.role === 'user') {
    return (
      <div className="ai-turn ai-turn-user">
        <span className="ai-turn-label">You</span>
        <div className="ai-turn-text">{turn.text}</div>
      </div>
    )
  }

  const done = !!turn.reply || !!turn.error
  const hasDiff = !!turn.diff
  const copyText = buildAgentCopyText(turn)

  const handleCopy = async () => {
    if (!copyText) return
    try {
      await navigator.clipboard.writeText(copyText)
      setCopyState('copied')
      window.setTimeout(() => setCopyState('idle'), 1500)
    } catch {
      setCopyState('failed')
      window.setTimeout(() => setCopyState('idle'), 1500)
    }
  }

  return (
    <div className="ai-turn ai-turn-agent">
      <div className="ai-turn-head">
        <span className="ai-turn-label">Agent</span>
        <button
          className="btn-ai-copy"
          onClick={handleCopy}
          disabled={!copyText}
          title={copyText ? 'Copy request and response for this agent turn' : 'Nothing to copy yet'}
        >
          {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed' : 'Copy'}
        </button>
      </div>

      {turn.steps && turn.steps.length > 0 && (
        <StepTimeline steps={turn.steps} done={done} />
      )}

      {/* Live token streaming — visible while the LLM is generating */}
      {!done && turn.streamingContent && (
        <div className="ai-streaming">
          <pre className="ai-streaming-text">{turn.streamingContent}<span className="ai-cursor">▌</span></pre>
        </div>
      )}

      {turn.error && (
        <div className="ai-turn-error">⚠ {turn.error}</div>
      )}

      {turn.reply && (
        <div className="ai-turn-reply">{turn.reply}</div>
      )}

      {hasDiff && (
        <>
          <div className="ai-section-label">Content stream diff</div>
          <DiffView unified={turn.diff!} />
        </>
      )}

      {turn.downloadUrl && (
        <div className="ai-turn-actions">
          <a
            className="btn-ai-download"
            href={turn.downloadUrl}
            download="modified.pdf"
            onClick={onDownload}
          >
            ↓ Download modified PDF
          </a>
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ provider selector

interface SelectorProps {
  config: AgentConfigResponse | null
  provider: string
  model: string
  onProviderChange: (p: string) => void
  onModelChange: (m: string) => void
}

const ProviderSelector: React.FC<SelectorProps> = ({
  config, provider, model, onProviderChange, onModelChange,
}) => {
  if (!config) return null
  const provInfo = config.providers.find(p => p.id === provider)
  const models = provInfo?.models ?? []

  return (
    <div className="ai-selector">
      <select
        value={provider}
        onChange={e => {
          const prov = e.target.value
          onProviderChange(prov)
          const pm = config.providers.find(p => p.id === prov)
          onModelChange(pm?.models[0] ?? '')
        }}
        className="ai-select"
      >
        {config.providers.map(p => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>
      <select
        value={model}
        onChange={e => onModelChange(e.target.value)}
        className="ai-select"
      >
        {models.map(m => <option key={m} value={m}>{m}</option>)}
      </select>
    </div>
  )
}

// ------------------------------------------------------------------ main pane

interface Props {
  uploadId: string
  objNum: number
  objGen: number
}

const AiChatPane: React.FC<Props> = ({ uploadId, objNum, objGen }) => {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [config, setConfig] = useState<AgentConfigResponse | null>(null)
  const [provider, setProvider] = useState('openai')
  const [model, setModel] = useState('gpt-4o-mini')
  const [canUndo, setCanUndo] = useState(false)
  const [debugHttp, setDebugHttp] = useState(true)

  const scrollRef = useRef<HTMLDivElement>(null)
  const esRef = useRef<EventSource | null>(null)

  // Load config on mount
  useEffect(() => {
    fetchAgentConfig().then(cfg => {
      if (!cfg) return
      setConfig(cfg)
      setProvider(cfg.default_provider)
      setModel(cfg.default_model)
    })
  }, [])

  // Reset on object change
  useEffect(() => {
    setTurns([])
    setCanUndo(false)
    setInput('')
    esRef.current?.close()
  }, [uploadId, objNum, objGen])

  // Scroll to bottom whenever turns change
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [turns])

  const appendStep = useCallback((step: AgentStep) => {
    // llm_chunk events are not shown in the timeline — they accumulate as
    // streaming text that disappears once the final reply arrives.
    if (step.type === 'llm_chunk') {
      setTurns(prev => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'agent') {
          next[next.length - 1] = {
            ...last,
            streamingContent: (last.streamingContent ?? '') + (step.text ?? ''),
          }
          return next
        }
        return prev
      })
      return
    }
    setTurns(prev => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (last?.role === 'agent') {
        next[next.length - 1] = {
          ...last,
          steps: [...(last.steps ?? []), step],
        }
        return next
      }
      return prev
    })
  }, [])

  const finishTurn = useCallback((data: { reply?: string; diff?: string; download_url?: string; error?: string }) => {
    setTurns(prev => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (last?.role === 'agent') {
        next[next.length - 1] = {
          ...last,
          reply: data.reply,
          diff: data.diff,
          downloadUrl: data.download_url,
          error: data.error,
          streamingContent: undefined,
        }
        return next
      }
      return prev
    })
    setBusy(false)
    if (data.download_url) setCanUndo(true)
  }, [])

  const sendMessage = useCallback(() => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)

    // Build history from prior turns
    const history = turns.flatMap<{ role: string; content: string }>(t => {
      if (t.role === 'user' && t.text) return [{ role: 'user', content: t.text }]
      if (t.role === 'agent' && t.reply) return [{ role: 'assistant', content: t.reply }]
      return []
    })

    // Add user turn
    setTurns(prev => [
      ...prev,
      { role: 'user', text },
      { role: 'agent', steps: [] },
    ])

    // Build SSE URL
    const params = new URLSearchParams({
      upload_id: uploadId,
      obj_num: String(objNum),
      obj_gen: String(objGen),
      message: text,
      history: JSON.stringify(history),
      provider,
      model,
      debug_http: String(debugHttp),
    })
    const url = `/api/agent/chat?${params}`

    const es = new EventSource(url)
    esRef.current = es

    es.addEventListener('step', (e: MessageEvent) => {
      const step: AgentStep = JSON.parse(e.data)
      appendStep(step)
    })

    es.addEventListener('done', (e: MessageEvent) => {
      const data = JSON.parse(e.data)
      finishTurn(data)
      es.close()
    })

    es.addEventListener('error', (e: MessageEvent) => {
      try {
        const data = JSON.parse((e as any).data)
        finishTurn({ error: data.error })
      } catch {
        finishTurn({ error: 'Connection error' })
      }
      es.close()
    })
  }, [input, busy, turns, uploadId, objNum, objGen, provider, model, debugHttp, appendStep, finishTurn])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const handleUndo = useCallback(async () => {
    const result = await agentUndo(uploadId)
    if (result.ok) {
      setTurns(prev => prev.slice(0, -2)) // remove last user + agent turn
      setCanUndo(turns.length > 3)
    }
  }, [uploadId, turns.length])

  return (
    <div className="ai-pane">
      {/* Header */}
      <div className="ai-header">
        <span className="ai-title">✦ AI Edit</span>
        <span className="ai-subtitle">
          obj {objNum} {objGen} R
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
          {canUndo && (
            <button className="btn-ai-undo" onClick={handleUndo} title="Undo last edit">
              ↩ Undo
            </button>
          )}
          <ProviderSelector
            config={config}
            provider={provider}
            model={model}
            onProviderChange={setProvider}
            onModelChange={setModel}
          />
          <button
            className={`btn-ai-debug${debugHttp ? ' active' : ''}`}
            onClick={() => setDebugHttp(v => !v)}
            title={debugHttp ? 'Debug mode on — click to disable' : 'Debug mode off — click to enable'}
          >
            {debugHttp ? '🐛 Debug' : '🐛'}
          </button>
        </div>
      </div>

      {/* Chat history */}
      <div className="ai-history" ref={scrollRef}>
        {turns.length === 0 && (
          <div className="ai-placeholder">
            Describe what you want to change in this content stream.
            <br />
            <span className="ai-placeholder-example">
              e.g. "Move the heading down by 30 points" or "Change the font color to red"
            </span>
          </div>
        )}
        {turns.map((turn, i) => (
          <TurnView key={i} turn={turn} />
        ))}
      </div>

      {/* Input */}
      <div className="ai-input-row">
        <textarea
          className="ai-input"
          rows={2}
          placeholder="Describe a change to make…  (Enter to send, Shift+Enter for newline)"
          value={input}
          disabled={busy}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          className="btn-ai-send"
          onClick={sendMessage}
          disabled={busy || !input.trim()}
        >
          {busy ? '…' : 'Send'}
        </button>
      </div>
    </div>
  )
}

export default AiChatPane
