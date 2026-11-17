export async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { ...options, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...options.headers } })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请重试')
  }
  return response.json() as Promise<T>
}
export const post = <T>(url: string, body: unknown = {}) => api<T>(url, { method: 'POST', body: JSON.stringify(body) })
