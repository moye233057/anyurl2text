import { useEffect, useRef, useState } from 'react'
import ResultPanel from './ResultPanel'
import { loadPresets, presetKey, type Preset } from './presets'
import { fetchHealth, runConvert } from './api'
import type { ConvertResponse } from './types'

export default function App() {
  const [modelKeyConfigured, setModelKeyConfigured] = useState(true)
  const [mode, setMode] = useState<'url' | 'file'>('url')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [query, setQuery] = useState('')
  const [ocr, setOcr] = useState(false)
  const [asr, setAsr] = useState(false)
  const [llm, setLlm] = useState(false)
  const [presets, setPresets] = useState<Preset[] | null>(null)
  const [presetProblems, setPresetProblems] = useState<string[]>([])

  const [result, setResult] = useState<ConvertResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [transportError, setTransportError] = useState('')
  const [activePresetKey, setActivePresetKey] = useState('')
  const urlRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    fetchHealth()
      .then((health) => setModelKeyConfigured(health.model_key_configured))
      .catch(() => setModelKeyConfigured(true))
  }, [])

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
          ocr,
          asr,
          llm,
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
          <div className="region-sub">同一请求携带能力开关。</div>

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

            <div className="field">
              <label className="field-label">扩展能力</label>
              <div className="cap-toggles">
                <label className="cap-item">
                  <input type="checkbox" checked={ocr} onChange={(e) => setOcr(e.target.checked)} />
                  OCR
                </label>
                <label className="cap-item">
                  <input type="checkbox" checked={asr} onChange={(e) => setAsr(e.target.checked)} />
                  ASR
                </label>
                <label className="cap-item">
                  <input type="checkbox" checked={llm} onChange={(e) => setLlm(e.target.checked)} />
                  LLM
                </label>
              </div>
              <p className="hint">看图需要同时开 OCR 与 LLM；摘要只需 LLM；音视频转写只需 ASR。</p>
            </div>

            <div className="preset-box">
              <div className="preset-head">
                预设样例
                <span className="hint">本仓样例走本地上传；远程条目填入 URL</span>
              </div>
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
            </div>

            <div className="field">
              <label className="field-label">
                问题（可选）
                <span className="hint">仅 LLM 开启时生效</span>
              </label>
              <textarea
                value={query}
                placeholder="例如：这份文档里的报名截止时间是什么？"
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>

            {transportError && <div className="error-box">{transportError}</div>}

            {!modelKeyConfigured && llm && (
              <p className="inline-warn">
                后端未配置 DEEPSEEK_API_KEY，开启 LLM / 看图时会失败并说明原因。
              </p>
            )}
          </div>
        </aside>

        <ResultPanel
          title="转换结果"
          subtitle="确定性抽取 + 已开启且可用的扩展。"
          result={result}
          loading={loading}
          onRun={run}
          canRun={canRun}
        />
      </div>
    </div>
  )
}
