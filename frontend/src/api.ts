import type { ConvertResponse, Health } from './types'

// 后端地址走 Vite 代理，前端只认相对路径
const BASE = '/api'

export interface ConvertInput {
  mode: 'url' | 'file'
  url: string
  file: File | null
  query: string
  /** OCR 提供商：false 不用；字符串是提供商 id（see configs/extensions.py）。同一请求只能选一个。 */
  ocr: boolean | string
  asr: boolean
  llm: boolean
}

async function parseResponse(response: Response): Promise<ConvertResponse> {
  // 后端在失败时也会返回完整的响应体，所以这里不按状态码丢 body
  const payload = (await response.json()) as ConvertResponse
  return payload
}

export async function runConvert(input: ConvertInput): Promise<ConvertResponse> {
  const endpoint = input.mode === 'url' ? '/convert' : '/convert/file'

  if (input.mode === 'url') {
    const response = await fetch(`${BASE}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: input.url,
        query: input.query || null,
        ocr: input.ocr,
        asr: input.asr,
        llm: input.llm,
      }),
    })
    return parseResponse(response)
  }

  const form = new FormData()
  if (input.file) {
    form.append('file', input.file)
  }
  if (input.query) {
    form.append('query', input.query)
  }
  form.append('ocr', String(input.ocr))
  form.append('asr', String(input.asr))
  form.append('llm', String(input.llm))
  const response = await fetch(`${BASE}${endpoint}`, { method: 'POST', body: form })
  return parseResponse(response)
}

/** 读健康 + 能力清单。refresh=true 时忽略后端探活缓存强制重探（点「刷新可用性」才用）。 */
export async function fetchHealth(refresh = false): Promise<Health> {
  const response = await fetch(`${BASE}/probe/health${refresh ? '?refresh=true' : ''}`)
  return (await response.json()) as Health
}

export interface ProviderTestResult {
  available: boolean
  reason?: string | null
  detail?: string | null
}

/** 主动测一次某个提供商。API 那一条会真发一次最小请求（花极小 token），侧车只探 /health。 */
export async function testProvider(capability: string, provider: string): Promise<ProviderTestResult> {
  const response = await fetch(`${BASE}/probe/test/${capability}/${provider}`, { method: 'POST' })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  return (await response.json()) as ProviderTestResult
}
