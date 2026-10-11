import { useCallback, useEffect, useRef, useState } from 'react'
import ResultPanel from './ResultPanel'
import { loadPresets, presetKey, type Preset } from './presets'
import { fetchHealth, runConvert, testProvider } from './api'
import type { CapabilityStatus, ConvertResponse } from './types'

type Capabilities = Record<string, CapabilityStatus> | null

/** 能力固定这三块，顺序即界面顺序；名字与提供商名单的唯一事实源在后端 configs/extensions.py。 */
const CAPABILITY_ORDER = ['llm', 'ocr', 'asr'] as const

const CAPABILITY_LABELS: Record<string, string> = {
  llm: 'LLM',
  ocr: 'OCR',
  asr: 'ASR',
}

function providersOf(capabilities: Capabilities, capability: string) {
  return capabilities?.[capability]?.providers ?? []
}

/** 一个选项一行：名称 + 测试按钮 + 可选/不可选；不可用整行置灰，原因挂在 title 上（悬停可见）。 */
function CapabilityRow(props: {
  capability: string
  id: string
  label: string
  available: boolean
  reason?: string | null
  selected: boolean
  onSelect: () => void
  onTest?: () => void
  testing: boolean
  note?: string
}) {
  const classes = ['cap-row']
  if (!props.available) classes.push('off')
  if (props.selected) classes.push('on')
  return (
    <div className="cap-row-wrap">
      <div className={classes.join(' ')} title={props.available ? undefined : props.reason ?? undefined}>
        <label className="cap-row-pick">
          <input
            type="radio"
            name={props.capability}
            checked={props.selected}
            disabled={!props.available}
            onChange={props.onSelect}
          />
          <span className="cap-row-label">{props.label}</span>
        </label>
        {props.onTest && (
          <button type="button" className="link-button" disabled={props.testing} onClick={props.onTest}>
            {props.testing ? '测试中…' : '测试'}
          </button>
        )}
        <span className={props.available ? 'cap-state' : 'cap-state off'}>
          {props.available ? '可选' : '不可选'}
        </span>
      </div>
      {props.note && <p className="cap-note">{props.note}</p>}
    </div>
  )
}

/** 可折叠的能力卡片：默认收起，标题上直接给「几个能用」，不占左侧版面。 */
function CapabilityCard(props: {
  capabilities: Capabilities
  selected: Record<string, string>
  onSelect: (capability: string, id: string) => void
  onRefresh: () => void
  onTest: (capability: string, provider: string) => void
  testing: string
  notes: Record<string, string>
}) {
  const all = CAPABILITY_ORDER.flatMap((capability) => providersOf(props.capabilities, capability))
  const usable = all.filter((provider) => provider.available).length
  return (
    <details className="cap-card">
      <summary className="cap-card-head">
        扩展能力
        <span className="hint">{all.length === 0 ? '读取中…' : `可用 ${usable} / ${all.length}`}</span>
      </summary>
      <div className="cap-rows">
        {CAPABILITY_ORDER.map((capability) => (
          <div key={capability} className="cap-group">
            <div className="cap-group-title">{CAPABILITY_LABELS[capability]}</div>
            <CapabilityRow
              capability={capability}
              id=""
              label="不用"
              available
              selected={props.selected[capability] === ''}
              onSelect={() => props.onSelect(capability, '')}
              testing={false}
            />
            {providersOf(props.capabilities, capability).map((provider) => {
              const key = `${capability}/${provider.id}`
              return (
                <CapabilityRow
                  key={key}
                  capability={capability}
                  id={provider.id}
                  label={provider.label}
                  available={provider.available}
                  reason={provider.reason}
                  selected={props.selected[capability] === provider.id}
                  onSelect={() => props.onSelect(capability, provider.id)}
                  onTest={() => props.onTest(capability, provider.id)}
                  testing={props.testing === key}
                  note={props.notes[key]}
                />
              )
            })}
          </div>
        ))}
        <button type="button" className="link-button" onClick={props.onRefresh}>
          刷新可用性
        </button>
      </div>
    </details>
  )
}

export default function App() {
  const [capabilities, setCapabilities] = useState<Capabilities>(null)
  const [selected, setSelected] = useState<Record<string, string>>({ llm: '', ocr: '', asr: '' })
  const [testing, setTesting] = useState('')
  const [testNotes, setTestNotes] = useState<Record<string, string>>({})
  const [mode, setMode] = useState<'url' | 'file'>('url')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [query, setQuery] = useState('')
  const [presets, setPresets] = useState<Preset[] | null>(null)
  const [presetProblems, setPresetProblems] = useState<string[]>([])

  const [result, setResult] = useState<ConvertResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [transportError, setTransportError] = useState('')
  const [activePresetKey, setActivePresetKey] = useState('')
  const urlRef = useRef<HTMLTextAreaElement>(null)

  const refreshHealth = useCallback(() => {
    fetchHealth()
      .then((health) => setCapabilities(health.capabilities ?? null))
      .catch(() => setCapabilities(null))
  }, [])

  useEffect(() => {
    refreshHealth()
  }, [refreshHealth])

  useEffect(() => {
    loadPresets().then((loaded) => {
      setPresets(loaded.presets)
      setPresetProblems(loaded.problems)
    })
  }, [])

  useEffect(() => {
    const fitUrlBox = () => {
      const element = urlRef.current
      if (!element) return
      element.style.height = 'auto'
      const border = element.offsetHeight - element.clientHeight
      element.style.height = `${element.scrollHeight + border}px`
    }
    fitUrlBox()
    window.addEventListener('resize', fitUrlBox)
    return () => window.removeEventListener('resize', fitUrlBox)
  }, [url, mode])

  const canRun = mode === 'url' ? url.trim().length > 0 : file !== null

  async function runTest(capability: string, provider: string) {
    const key = `${capability}/${provider}`
    setTesting(key)
    setTestNotes((notes) => ({ ...notes, [key]: '正在测试…' }))
    try {
      const outcome = await testProvider(capability, provider)
      setTestNotes((notes) => ({
        ...notes,
        [key]: outcome.available ? outcome.detail || '测试通过' : outcome.reason || '测试失败',
      }))
      if (outcome.available) {
        // 测通就选上：点「测试」本来就是为了用它
        setSelected((current) => ({ ...current, [capability]: provider }))
      }
      refreshHealth()
    } catch (error) {
      setTestNotes((notes) => ({ ...notes, [key]: `测试失败：${String(error)}` }))
    } finally {
      setTesting('')
    }
  }

  async function run() {
    if (!canRun) return
    setTransportError('')
    setLoading(true)
    try {
      setResult(
        await runConvert({
          mode,
          url: url.trim(),
          file,
          query: query.trim(),
          ocr: selected.ocr === '' ? false : selected.ocr,
          asr: selected.asr !== '',
          llm: selected.llm !== '',
        }),
      )
    } catch (error) {
      setTransportError(`请求失败：${String(error)}`)
    } finally {
      setLoading(false)
    }
  }

  async function applyPreset(preset: Preset) {
    setTransportError('')
    setActivePresetKey(presetKey(preset))
    if (preset.file) {
      setMode('file')
      setUrl('')
      try {
        const response = await fetch(`${import.meta.env.BASE_URL}${preset.file}`)
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }
        const blob = await response.blob()
        const name = preset.file.split('/').pop() || 'sample'
        setFile(new File([blob], name, { type: blob.type || 'application/octet-stream' }))
      } catch (error) {
        setFile(null)
        setTransportError(`预设样例加载失败（${preset.file}）：${String(error)}`)
      }
      return
    }

    setMode('url')
    setFile(null)
    setUrl(preset.url || '')
    try {
      await navigator.clipboard.writeText(preset.url || '')
    } catch {
      // 非安全上下文里剪贴板会失败；主要动作是填入输入框
    }
  }

  return (
    <div className="app">
      <div className="layout">
        <aside className="region">
          <div className="region-head">
            <span className="title">输入</span>
          </div>

          <div className="region-body">
            <div className="mode-switch">
              <button className={mode === 'url' ? 'active' : ''} onClick={() => setMode('url')}>
                远程 URL
              </button>
              <button className={mode === 'file' ? 'active' : ''} onClick={() => setMode('file')}>
                本地文件
              </button>
            </div>

            {mode === 'url' ? (
              <div className="field">
                <label className="field-label">文件或网页地址</label>
                <textarea
                  ref={urlRef}
                  className="url-field"
                  rows={1}
                  value={url}
                  placeholder="https://example.com/sample.pdf"
                  onChange={(event) => setUrl(event.target.value.replace(/[\r\n]+/g, ''))}
                />
              </div>
            ) : (
              <div className="field">
                <label className="field-label">选择本地文件</label>
                <input type="file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
                {/* 预设样例直接写入 file 状态，而浏览器不允许脚本改 file input 的显示，
                    所以这里自己报一次已选文件——否则点完预设，上面还写着「未选择任何文件」。 */}
                {file && <p className="hint">已选：{file.name}</p>}
              </div>
            )}

            <CapabilityCard
              capabilities={capabilities}
              selected={selected}
              onSelect={(capability, id) => setSelected((current) => ({ ...current, [capability]: id }))}
              onRefresh={refreshHealth}
              onTest={runTest}
              testing={testing}
              notes={testNotes}
            />

            <details className="preset-box" open>
              <summary className="preset-head">预设样例</summary>
              <div className="preset-list">
                {presets?.map((preset) => {
                  const key = presetKey(preset)
                  return (
                    <button
                      key={key}
                      type="button"
                      className={key === activePresetKey ? 'preset-item active' : 'preset-item'}
                      onClick={() => applyPreset(preset)}
                    >
                      <span className="preset-kind">{preset.kind}</span>
                      <span className="preset-desc">{preset.desc}</span>
                    </button>
                  )
                })}
                {presets?.length === 0 && presetProblems.length === 0 && (
                  <p className="empty">配置文件里没有样例</p>
                )}
              </div>
              {presetProblems.length > 0 && (
                <div className="note-box">
                  预设配置有问题：
                  <ul>
                    {presetProblems.map((problem, index) => (
                      <li key={index}>{problem}</li>
                    ))}
                  </ul>
                </div>
              )}
            </details>

            <div className="field">
              <label className="field-label">问题（可选）</label>
              <textarea
                value={query}
                placeholder="例如：这份文档里的报名截止时间是什么？"
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>

            {transportError && <div className="error-box">{transportError}</div>}
          </div>
        </aside>

        <ResultPanel
          title="转换结果"
          subtitle="确定性抽取 + 已选中且可用的扩展。"
          result={result}
          loading={loading}
          onRun={run}
          canRun={canRun}
        />
      </div>
    </div>
  )
}
