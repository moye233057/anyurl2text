import { useEffect, useRef, useState } from 'react'
import ResultPanel from './ResultPanel'
import { loadPresets, type Preset } from './presets'
import { fetchHealth, runPipeline } from './api'
import type { ConvertResponse } from './types'

// 两条管线各有各的运行按钮，点哪个跑哪个：
// 不再强制顺序执行，各自的耗时也只受自己影响，对比更直接。
export default function App() {
  const [modelKeyConfigured, setModelKeyConfigured] = useState(true)
  const [mode, setMode] = useState<'url' | 'file'>('url')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [query, setQuery] = useState('')
  // null 表示还没取回来：这时不渲染列表，也不说「配置里没有样例」——那是取回来才成立的话
  const [presets, setPresets] = useState<Preset[] | null>(null)
  const [presetProblems, setPresetProblems] = useState<string[]>([])

  const [extractResult, setExtractResult] = useState<ConvertResponse | null>(null)
  const [understandResult, setUnderstandResult] = useState<ConvertResponse | null>(null)
  const [extractLoading, setExtractLoading] = useState(false)
  const [understandLoading, setUnderstandLoading] = useState(false)
  const [transportError, setTransportError] = useState('')
  const urlRef = useRef<HTMLTextAreaElement>(null)

  // 只在「后端起来了但没配 Key」时提示一句；后端没起来就不猜，让请求自己报错。
  useEffect(() => {
    fetchHealth()
      .then((health) => setModelKeyConfigured(health.model_key_configured))
      .catch(() => setModelKeyConfigured(true))
  }, [])

  // 预设样例从顶层配置文件取（public/presets.config.js）：改配置不用改代码，刷新即生效。
  // loadPresets 自己兜住失败，所以这里不需要 catch。
  useEffect(() => {
    loadPresets().then((result) => {
      setPresets(result.presets)
      setPresetProblems(result.problems)
    })
  }, [])

  // URL 框按填入的地址自己长高：短地址一行，长地址折行显示完整，
  // 只有确实过长的地址才在框内滚动（高度上限见 .url-field 的 max-height）。
  // 量 scrollHeight 而不是按字符数估算：行宽取决于列宽与字体，估不准。
  // 加边框高度是为了抵消全局的 border-box——只写 scrollHeight 会少算上下边框，
  // 每个地址都差 2px 溢出，白出一条滚动条。
  useEffect(() => {
    const fitUrlBox = () => {
      const element = urlRef.current
      if (!element) return
      element.style.height = 'auto'
      const border = element.offsetHeight - element.clientHeight
      element.style.height = `${element.scrollHeight + border}px`
    }
    fitUrlBox()
    // 窗口变窄时同一段地址会折成更多行，高度得跟着重算
    window.addEventListener('resize', fitUrlBox)
    return () => window.removeEventListener('resize', fitUrlBox)
  }, [url, mode])

  const canRun = mode === 'url' ? url.trim().length > 0 : file !== null

  async function runOne(pipeline: 'extract' | 'understand') {
    if (!canRun) return
    const input = { mode, url: url.trim(), file, query: query.trim() }
    // 两个 setState 的类型完全相同，取其中哪一个都不会有问题
    const setLoading = pipeline === 'extract' ? setExtractLoading : setUnderstandLoading
    const setResult = pipeline === 'extract' ? setExtractResult : setUnderstandResult

    setTransportError('')
    setLoading(true)
    try {
      setResult(await runPipeline(pipeline, input))
    } catch (error) {
      setTransportError(`${pipeline} 请求失败：${String(error)}`)
    } finally {
      setLoading(false)
    }
  }

  async function applyPreset(preset: Preset) {
    setMode('url')
    setUrl(preset.url)
    try {
      await navigator.clipboard.writeText(preset.url)
    } catch {
      // 非安全上下文里剪贴板会失败；主要动作是填入输入框，不该被它打断
    }
  }

  return (
    <div className="app">
      <div className="layout">
        <aside className="region">
          <div className="region-head">
            <span className="title">输入</span>
          </div>
          <div className="region-sub">两条管线共用同一份输入。</div>

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
                  // 换行是 textarea 自带的输入方式，而地址里不该有回车：
                  // 换成可折行的框之后得自己把换行挡掉（单行 input 不会有这个问题）。
                  onChange={(event) => setUrl(event.target.value.replace(/[\r\n]+/g, ''))}
                />
              </div>
            ) : (
              <div className="field">
                <label className="field-label">选择本地文件</label>
                <input type="file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
              </div>
            )}

            {mode === 'url' && (
              <div className="preset-box">
                <div className="preset-head">
                  预设样例
                  <span className="hint">点击即复制并填入</span>
                </div>
                <div className="preset-list">
                  {presets?.map((preset) => (
                    <button
                      key={preset.url}
                      type="button"
                      className={preset.url === url ? 'preset-item active' : 'preset-item'}
                      onClick={() => applyPreset(preset)}
                    >
                      <span className="preset-kind">{preset.kind}</span>
                      <span className="preset-desc">{preset.desc}</span>
                    </button>
                  ))}
                  {presets?.length === 0 && presetProblems.length === 0 && (
                    <p className="empty">配置文件里没有样例</p>
                  )}
                </div>
                {presetProblems.length > 0 && (
                  <div className="note-box">
                    {/* 这句话对「整份配置没取回来」和「个别条目写错」都成立，细节在下面的列表里 */}
                    预设配置有问题：
                    <ul>
                      {presetProblems.map((problem, index) => (
                        <li key={index}>{problem}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            <div className="field">
              <label className="field-label">
                问题（可选）
                <span className="hint">只对模型增强解析生效</span>
              </label>
              <textarea
                value={query}
                placeholder="例如：这份文档里的报名截止时间是什么？"
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>

            {transportError && <div className="error-box">{transportError}</div>}

            {!modelKeyConfigured && (
              <p className="inline-warn">
                后端未配置 DEEPSEEK_API_KEY，模型增强解析会失败并说明原因；纯代码解析不受影响。
              </p>
            )}
          </div>
        </aside>

        <ResultPanel
          pipeline="extract"
          title="纯代码解析"
          subtitle="下载 → 识别类型 → 按格式解析，不调用模型。"
          result={extractResult}
          loading={extractLoading}
          onRun={() => runOne('extract')}
          canRun={canRun}
        />
        <ResultPanel
          pipeline="understand"
          title="模型增强解析"
          subtitle="复用同一段抽取，再叠加认字、内容描述与摘要。"
          result={understandResult}
          loading={understandLoading}
          onRun={() => runOne('understand')}
          canRun={canRun}
        />
      </div>
    </div>
  )
}
