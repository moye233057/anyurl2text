// 预设样例的加载与校验。
//
// 数据源是顶层配置文件 `frontend/public/presets.config.js`：页面启动时按 URL 动态 import
// 一个 ES 模块（因此它必须是 `export default [...]` 的 JS，不能换成 JSON）。
// 改配置不用改代码；dev 下刷新页面即生效，用构建产物时要重新 build 或改 dist 里那份副本。
// 这里只做「取回来 + 校验 + 说明跳过原因」，不持有任何样例数据——数据只有配置文件一处。
//
// 取不回来（文件被删、语法错、路径不对）与个别条目写错都不该让界面白屏：
// 前者返回一条说明，后者跳过坏条目，两者都通过 problems 交给界面显示，不静默吞掉。

export interface Preset {
  kind: string
  desc: string
  /** 远程 URL 预设：填入输入框后走下载口 */
  url?: string
  /** 本站静态样例：相对站点根的路径，点击后走本地文件上传口 */
  file?: string
}

export interface PresetLoadResult {
  presets: Preset[]
  /** 配置本身或某个条目的问题，逐条说明；空数组表示没有可报的问题 */
  problems: string[]
}

// public/ 下的文件在开发服务器与构建产物里都落在站点根路径
const CONFIG_PATH = 'presets.config.js'
const CONFIG_URL = `${import.meta.env.BASE_URL}${CONFIG_PATH}`

function asText(record: Record<string, unknown>, name: string): string | null {
  const value = record[name]
  return typeof value === 'string' ? value.trim() : null
}

function isHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    return false
  }
}

function isSiteRelativeFile(value: string): boolean {
  if (!value || value.startsWith('/') || value.includes('://') || value.includes('\\')) {
    return false
  }
  const parts = value.split('/')
  return parts.every((part) => part.length > 0 && part !== '.' && part !== '..')
}

function readPreset(entry: unknown, index: number, problems: string[]): Preset | null {
  const label = `第 ${index + 1} 条`
  if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) {
    problems.push(`${label}不是对象，已跳过`)
    return null
  }

  const record = entry as Record<string, unknown>
  const kind = asText(record, 'kind')
  // desc 允许是空串（只显示标题），但字段必须存在——写成 description 这类名字要能被发现
  const desc = 'desc' in record ? asText(record, 'desc') : null
  const hasUrlField = 'url' in record
  const hasFileField = 'file' in record
  const url = hasUrlField ? asText(record, 'url') : null
  const file = hasFileField ? asText(record, 'file') : null

  if (kind === null || desc === null) {
    const missing = [
      ['kind', kind],
      ['desc', desc],
    ]
      .filter(([, value]) => value === null)
      .map(([name]) => name)
    problems.push(`${label}的 ${missing.join(' / ')} 缺失或不是字符串，已跳过`)
    return null
  }

  const urlValue = url && url.length > 0 ? url : null
  const fileValue = file && file.length > 0 ? file : null
  if (urlValue && fileValue) {
    problems.push(`${label}同时写了 url 与 file，已跳过（二者只能选一个）`)
    return null
  }
  if (!urlValue && !fileValue) {
    problems.push(`${label}需要 url 或 file 其中一个，已跳过`)
    return null
  }
  if (urlValue && !isHttpUrl(urlValue)) {
    problems.push(`${label}的 url 不是 http(s) 地址（${urlValue}），已跳过`)
    return null
  }
  if (fileValue && !isSiteRelativeFile(fileValue)) {
    problems.push(
      `${label}的 file 必须是相对站点根的路径、不能含 .. 或盘符（${fileValue}），已跳过`,
    )
    return null
  }
  return {
    kind,
    desc,
    ...(urlValue ? { url: urlValue } : {}),
    ...(fileValue ? { file: fileValue } : {}),
  }
}

export function presetKey(preset: Preset): string {
  return preset.file ? `file:${preset.file}` : `url:${preset.url}`
}

export async function loadPresets(): Promise<PresetLoadResult> {
  let module: { default?: unknown }
  try {
    // 带时间戳是刻意的：这类静态文件会被浏览器缓存，缓存命中就看不到刚改的配置，
    // 而「改完刷新就生效」正是这个文件存在的理由。
    module = await import(/* @vite-ignore */ `${CONFIG_URL}?t=${Date.now()}`)
  } catch (error) {
    return { presets: [], problems: [`预设配置没加载成功（${CONFIG_URL}）：${String(error)}`] }
  }

  const entries = module.default
  if (!Array.isArray(entries)) {
    return {
      presets: [],
      problems: [`预设配置要写成 \`export default [ ... ]\`，当前不是数组（${CONFIG_PATH}）`],
    }
  }

  const problems: string[] = []
  const presets: Preset[] = []
  entries.forEach((entry, index) => {
    const preset = readPreset(entry, index, problems)
    if (preset) {
      presets.push(preset)
    }
  })
  for (const problem of problems) {
    console.error(`预设配置：${problem}`)
  }
  return { presets, problems }
}
