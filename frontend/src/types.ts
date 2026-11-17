export type User = { username: string; display_name: string | null; role: string }
export type Conversation = { id: string; title: string; created_at: string; updated_at: string }
export type QueryResult = { success: boolean; sql?: string; columns?: string[]; rows?: unknown[][]; row_count?: number; description?: string | null; described_rows?: number; chart?: boolean; error?: string; validation_errors?: string[] }
export type Message = { id: string; role: 'user' | 'assistant'; content: string; result?: QueryResult | null; created_at: string }
export type DataResult = { rows?: unknown[][]; columns?: string[]; row_count?: number; truncated?: boolean }
export type CaseResult = { id: string; category: string; question: string; status: string; reason: string; corrections?: number; sql?: string; reference_sql?: string; actual?: DataResult; expected?: DataResult; attempts?: unknown[] }
export type Run = { id: string; status: string; created_at: number; model: string; completed: number; total: number; warning?: string; results: CaseResult[] }
export type RunList = { suite: {name: string}; count: number; runs: Run[] }
