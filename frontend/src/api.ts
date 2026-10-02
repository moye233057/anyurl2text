import type { ConvertResponse, Health } from './types'

// 后端地址走 Vite 代理，前端只认相对路径
const BASE = '/api'

export type Pipeline = 'extract' | 'understand'

export interface ConvertInput {
  mode: 'url' | 'file'
  url: string
  file: File | null
  query: string
}

async function parseResponse(response: Response): Promise<ConvertResponse> {
  // 后端在失败时也会返回完整的响应体，所以这里不按状态码丢 body
  const payload = (await response.json()) as ConvertResponse
  return payload
}

export async function runPipeline(pipeline: Pipeline, input: ConvertInput): Promise<ConvertResponse> {
  const endpoint = input.mode === 'url' ? `/${pipeline}` : `/${pipeline}/file`

  if (input.mode === 'url') {
    const response = await fetch(`${BASE}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: input.url, query: input.query || null }),
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
  const response = await fetch(`${BASE}${endpoint}`, { method: 'POST', body: form })
  return parseResponse(response)
}

export async function fetchHealth(): Promise<Health> {
  const response = await fetch(`${BASE}/probe/health`)
  return (await response.json()) as Health
}
