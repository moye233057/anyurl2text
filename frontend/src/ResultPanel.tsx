import type { ReactNode } from 'react'
import type { ConvertResponse, Usage } from './types'

interface Props {
  pipeline: 'extract' | 'understand'
  title: string
  subtitle: string
  result: ConvertResponse | null
  loading: boolean
  /** 只跑这一条管线；两条管线各有各的按钮，互不牵动 */
  onRun: () => void
  /** 输入就绪才允许运行（没填地址时按钮禁用） */
  canRun: boolean
}

type Rows = Array<[string, string]>

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

// 区域内的一个固定子区块：标题固定，超出部分由区域整体的滚动条接管。
// 不给区块单独加滚动条——那样统计/元信息/用量会把高度吃光，留给长文本的只剩几十像素。
function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="block">
      <div className="block-title">{title}</div>
      <div className="block-body">{children}</div>
    </section>
  )
}

function KvGrid({ rows }: { rows: Rows }) {
  return (
    <div className="kv-grid">
      {rows.map(([key, value]) => (
        <div key={key}>
          <span className="k">{key}</span>
          <span className="v">{value}</span>
        </div>
      ))}
    </div>
  )
}

function buildStatsRows(result: ConvertResponse): Rows {
  // 文件类型（契约里的 type 字段）是这份结果的第一个事实，比耗时更该先看到
  const rows: Rows = [
    ['文件类型', result.type],
    ['合计', `${result.timings.total_ms} ms`],
    ['下载', `${result.timings.download_ms} ms`],
    ['解析', `${result.timings.parse_ms} ms`],
  ]
  // 纯代码管线不调模型，这一行恒为 0；只有真的调用过才值得显示
  if (result.timings.model_ms > 0) rows.push(['模型', `${result.timings.model_ms} ms`])
  rows.push(['content 字符数', String(result.content.length)])
  if (result.meta.ocr_text) rows.push(['识别文本字符数', String(result.meta.ocr_text.length)])
  if (result.meta.asr_text) rows.push(['转写文本字符数', String(result.meta.asr_text.length)])
  if (result.summary) rows.push(['摘要字符数', String(result.summary.length)])
  return rows
}

function buildMetaRows(result: ConvertResponse): Rows {
  const { meta } = result
  // 来源不放这里：它可能是一条很长的 URL，塞进网格会把整行顶高，单独整行显示
  const rows: Rows = [
    ['识别方式', meta.detector],
    ['扩展名', meta.ext ?? '未知'],
    ['大小', formatBytes(meta.size_bytes)],
  ]
  if (meta.mime) rows.push(['Content-Type', meta.mime])
  if (meta.pages != null) {
    rows.push([
      '页数',
      meta.pages_used != null && meta.pages_used !== meta.pages
        ? `${meta.pages_used} / ${meta.pages}`
        : String(meta.pages),
    ])
  }
  if (meta.images != null) {
    // 有图却一张都没处理，说明这批图被别的路径覆盖了（典型是扫描件的页面渲染）。
    // 只显示数字会让人以为「找到了 3 张图并且处理过」，所以把"未处理"标出来。
    const label =
      meta.images_used === 0 && meta.images > 0
        ? `${meta.images}（本次未处理）`
        : meta.images_used
          ? `${meta.images_used} / ${meta.images}`
          : String(meta.images)
    rows.push(['内嵌图', label])
  }
  if (meta.entry_count != null) rows.push(['条目数', String(meta.entry_count)])
  // 表头行数是源自己标的（<th> / <thead>），不是猜的；null 与 0 意思不同，所以只在非 null 时显示
  if (meta.table_header_rows != null) rows.push(['表头行数', String(meta.table_header_rows)])
  // 表类型照 anydoc 的 TableKind 口径：layout 是 1×1 的单格表（文本框里当容器用的排版壳）
  if (meta.table_kind) rows.push(['表类型', meta.table_kind === 'layout' ? '排版壳' : '数据表'])
  if (meta.image) {
    const image = meta.image
    if (image.width && image.height) rows.push(['尺寸', `${image.width} × ${image.height}`])
    if (image.format) rows.push(['格式', image.format])
    if (image.mode) rows.push(['色彩模式', image.mode])
    if (image.frames != null) rows.push(['帧数', String(image.frames)])
    Object.entries(image.exif).forEach(([key, value]) => rows.push([`EXIF ${key}`, value]))
  }
  if (meta.audio) {
    const audio = meta.audio
    if (audio.duration_seconds != null) rows.push(['时长', `${audio.duration_seconds.toFixed(1)} 秒`])
    if (audio.sample_rate != null) rows.push(['采样率', `${audio.sample_rate} Hz`])
    if (audio.channels != null) rows.push(['声道', String(audio.channels)])
    if (audio.format) rows.push(['音轨格式', audio.format])
  }
  if (meta.engine.length) rows.push(['解析器', meta.engine.join(', ')])
  // 命中的上界只报**机器名**：给人看的那句说明由后端按模板写进 notes，两处不复述同一句话
  if (meta.limits_hit.length) rows.push(['命中上界', meta.limits_hit.join(', ')])
  // 「哪几页没处理」是结构化事实，别让人从 pages_used 与页数自己推
  if (meta.unprocessed_pages.length) rows.push(['未处理页', meta.unprocessed_pages.join(', ')])
  if (meta.degrade_code) rows.push(['降级码', meta.degrade_code])
  return rows
}

function buildUsageRows(usage: Usage): Rows {
  return [
    ['模型', usage.model],
    ['调用次数', String(usage.calls)],
    ['输入 token', String(usage.prompt_tokens)],
    ['其中缓存命中', String(usage.cached_tokens)],
    ['输出 token', String(usage.completion_tokens)],
    ['其中思维链', String(usage.reasoning_tokens)],
    ['折算成本', `¥${usage.cost_cny.toFixed(6)}`],
    ['单价生效日', usage.price_effective_from ?? '-'],
  ]
}

// 一条管线的固定区域。两条管线的区块划分与顺序都不同：
// understand 的用量统计排在最前（先看到这次花了多少），摘要排在解析内容之前。
export default function ResultPanel({
  pipeline,
  title,
  subtitle,
  result,
  loading,
  onRun,
  canRun,
}: Props) {
  const showError = Boolean(result?.error)
  const hasParsedText = Boolean(result && (result.content || result.meta.ocr_text || result.meta.asr_text))
  const showMetaCopy =
    pipeline === 'understand' &&
    Boolean(
      result?.meta.ocr_text &&
        result.content.includes(result.meta.ocr_text.slice(0, Math.min(20, result.meta.ocr_text.length))),
    )

  return (
    <section className="region">
      <div className="region-head">
        <span className="title">{title}</span>
        {result?.meta.degraded && <span className="badge warn">降级</span>}
        <button className="run-button" onClick={onRun} disabled={!canRun || loading}>
          {loading ? '运行中…' : '运行'}
        </button>
      </div>
      <div className="region-sub">{subtitle}</div>

      {loading && <div className="region-note">处理中…</div>}
      {!loading && !result && <div className="region-note">点右上角「运行」开始</div>}

      {!loading && result && showError && (
        <div className="region-body">
          <div className="error-box">
            <div>{result.error?.message}</div>
            <code>{result.error?.code}</code>
          </div>
          <Block title="统计信息">
            <KvGrid rows={buildStatsRows(result)} />
          </Block>
        </div>
      )}

      {!loading && result && !showError && (
        <div className="region-body">
          {pipeline === 'understand' && (
            <Block title="用量统计">
              {result.usage ? (
                <KvGrid rows={buildUsageRows(result.usage)} />
              ) : (
                <p className="empty">（本次没有调用模型，无用量）</p>
              )}
            </Block>
          )}

          <Block title="统计信息">
            <KvGrid rows={buildStatsRows(result)} />
          </Block>

          <Block title="文件元信息">
            <div className="source-line">
              <span className="k">来源</span>
              <span className="v">{result.meta.source}</span>
            </div>
            {result.meta.degrade_reason && (
              <div className="note-box">
                降级原因：{result.meta.degrade_reason}
                {result.meta.degrade_code && <code>{result.meta.degrade_code}</code>}
              </div>
            )}
            {result.meta.model_error && (
              <div className="note-box">
                模型阶段失败：{result.meta.model_error.message}
                <code>{result.meta.model_error.code}</code>
              </div>
            )}
            <KvGrid rows={buildMetaRows(result)} />
            {result.meta.notes.length > 0 && (
              <div className="note-box">
                现场说明
                <ul>
                  {result.meta.notes.map((note, index) => (
                    <li key={index}>{note}</li>
                  ))}
                </ul>
              </div>
            )}
          </Block>

          {pipeline === 'understand' && (
            <Block title="内容摘要">
              {result.summary ? (
                <pre className="fill prose">{result.summary}</pre>
              ) : (
                <p className="empty">（本次没有产出摘要）</p>
              )}
            </Block>
          )}

          <Block title="解析内容">
            {result.content && (
              <>
                <div className="sub-label first">
                  content · {pipeline === 'understand' ? '正文（含模型转写）' : '纯代码抽取'}
                </div>
                <pre className="fill">{result.content}</pre>
              </>
            )}
            {showMetaCopy && result.meta.ocr_text && (
              <>
                <div className="sub-label">meta.ocr_text · 识别副本</div>
                <pre className="fill">{result.meta.ocr_text}</pre>
              </>
            )}
            {pipeline === 'understand' && result.meta.asr_text && (
              <>
                <div className={result.content ? 'sub-label' : 'sub-label first'}>
                  meta.asr_text · 转写副本
                </div>
                <pre className="fill">{result.meta.asr_text}</pre>
              </>
            )}
            {!hasParsedText && <p className="empty">（这份源没有可抽取的文本）</p>}
          </Block>
        </div>
      )}
    </section>
  )
}
