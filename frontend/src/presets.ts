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
  url: string
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
  const url = asText(record, 'url')
  const missing = [
    ['kind', kind],
    ['desc', desc],
    ['url', url],
  ]
    .filter(([, value]) => value === null)
    .map(([name]) => name)

  if (missing.length > 0) {
    problems.push(`${label}的 ${missing.join(' / ')} 缺失或不是字符串，已跳过`)
    return null
  }
  if (!isHttpUrl(url as string)) {
    problems.push(`${label}的 url 不是 http(s) 地址（${url}），已跳过`)
    return null
  }
  return { kind: kind as string, desc: desc as string, url: url as string }
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
