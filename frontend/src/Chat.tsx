import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowUp, Check, ChevronDown, Copy, Database, FileCode2, Table2, XCircle } from 'lucide-react'
import type { Message, QueryResult, DataResult } from './types'
const value = (v: unknown) => v === null ? 'NULL' : typeof v === 'object' ? JSON.stringify(v) : String(v)
export function DataTable({result}: {result: DataResult}) { const columns = result.columns || []; const rows = result.rows || []; return <div className="table-scroll"><table><thead><tr>{columns.map((c,i)=><th key={i}>{c}</th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={i}>{row.map((v,j)=><td key={j}>{value(v)}</td>)}</tr>)}</tbody></table>{!rows.length && <div className="table-empty">没有返回数据</div>}</div> }
function Sql({sql}: {sql: string}) { const [copied, setCopied] = useState(false); return <section className="result-section"><div className="result-heading"><span><FileCode2 size={16}/> 生成的 SQL</span><button className="text-button" onClick={async()=>{try {await navigator.clipboard.writeText(sql); setCopied(true); setTimeout(()=>setCopied(false),2000)} catch {setCopied(false)}}}>{copied ? <Check size={15}/> : <Copy size={15}/>} {copied?'已复制':'复制 SQL'}</button></div><pre className="sql-code"><code>{sql}</code></pre></section> }
function Chart({result}: {result: QueryResult}) { const rows = result.rows || []; const columns = result.columns || []; const numericIndex = columns.findIndex((_,i) => rows.some(row => typeof row[i] === 'number') && rows.every(row=>row[i] == null || typeof row[i] === 'number')); if (numericIndex < 0 || !rows.length) return null; const max = Math.max(...rows.map(row=>Math.abs(Number(row[numericIndex]) || 0)), 1); return <section className="result-section"><div className="result-heading"><span>数据图表</span><small>{columns[numericIndex]}</small></div><div className="chart">{rows.slice(0,15).map((row,i)=><div className="chart-row" key={i}><span>{columns.length > 1 ? value(row[0]) : i+1}</span><div className="bar-track"><div className="bar" style={{width: `${Math.abs(Number(row[numericIndex]) || 0)/max*100}%`}}/></div><strong>{value(row[numericIndex])}</strong></div>)}</div>{rows.length>15 && <small>图表显示前 15 行，完整展示数据见下表。</small>}</section> }
function AssistantMessage({message}: {message: Message}) {
  const r = message.result
  return <div className="message-row assistant-message"><div className="message-body"><div className="message-meta"><div className="message-avatar assistant-avatar"><Database size={16}/></div><strong>查询助手</strong></div>{!r ? <div className="legacy-content">{message.content}</div> : <div className="answer">
    {(r.sql || r.success) && <details className="query-details"><summary>查看查询过程 <ChevronDown size={16}/></summary><div className="query-details-content">{r.sql && <Sql sql={r.sql}/>} {r.success && <><section className="result-section"><div className="result-heading"><span><Table2 size={16}/> 查询结果</span><small>共 {r.row_count ?? 0} 行{(r.row_count ?? 0)>50?'，显示前 50 行':''}</small></div><DataTable result={r}/></section>{r.chart && <Chart result={r}/>}</>}</div></details>}
    {r.success ? <><p className="answer-description">{r.description || (r.description === null ? '查询已完成，但结果描述暂时不可用。你可以展开查看查询结果。' : '查询完成，请展开查看结果。')}</p>{r.description && r.described_rows !== undefined && r.described_rows < (r.row_count ?? 0) && <p className="description-scope">描述基于返回结果中的前 {r.described_rows} 行，共 {r.row_count} 行。</p>}</> : <p className="answer-status"><XCircle size={17}/> {r.error || '查询未完成'}</p>}
    {r.validation_errors && r.validation_errors.length > 0 && <p className="validation-errors">{r.validation_errors.join('；')}</p>}
  </div>}</div></div>
}
const examples = [
  { label: '统计数量', question: '统计「表名」的记录总数' },
  { label: '比较排行', question: '按「字段」分组统计，并按数量从高到低排序' },
  { label: '查看明细', question: '显示「表名」最近的 10 条记录' },
]

export function Chat({messages, loading, onSend, selectExample}: {messages: Message[]; loading: boolean; onSend: (text:string)=>void; selectExample?: string}) {
  const [input, setInput] = useState('')
  const end = useRef<HTMLDivElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { end.current?.scrollIntoView({behavior:'smooth', block:'end'}) }, [messages.length, loading])
  useEffect(() => { if (selectExample) setInput(selectExample) }, [selectExample])
  function submit(e: FormEvent) {
    e.preventDefault()
    const q = input.trim()
    if (q && !loading) { setInput(''); onSend(q) }
  }
  const composer = <div className="composer-wrap"><form className="composer" onSubmit={submit}><label className="sr-only" htmlFor="question">输入你的问题</label><textarea ref={textarea} id="question" rows={1} value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault(); e.currentTarget.form?.requestSubmit()}}} placeholder="输入你的问题，查询数据"/><button className="send-button" aria-label="发送问题" disabled={!input.trim()||loading}><ArrowUp size={19}/></button></form><p>结果由 AI 生成，请核对 SQL 与查询结果。按 Enter 发送，Shift + Enter 换行。</p></div>
  const empty = messages.length === 0 && !loading
  return <div className={`chat-layout ${empty ? 'chat-layout-empty' : ''}`}>
    <div className="chat-scroll"><div className="chat-content">{empty ? <div className="empty-chat"><div className="empty-chat-intro"><div className="workspace-mark"><Database size={21}/></div><h1>从一个问题开始</h1><p>用自然语言提问，探索你的数据。</p></div>{composer}<div className="suggestions"><span className="suggestions-label">试试这些问题模板</span><div className="suggestion-grid">{examples.map(({label,question})=><button className="suggestion-card" key={question} onClick={()=>{setInput(question);textarea.current?.focus()}}><span className="suggestion-category">{label}</span><span className="suggestion-question">{question}</span></button>)}</div><p className="suggestions-hint">选择模板后，将「表名」或「字段」替换为实际内容。</p></div></div> : <div className="messages">{messages.map(m=>m.role==='user'?<div className="message-row message-row-user" key={m.id}><div className="message-avatar user-avatar">我</div><div className="message-body"><div className="message-meta"><strong>你的问题</strong></div><p className="question-text">{m.content}</p></div></div>:<AssistantMessage key={m.id} message={m}/>)}{loading && <div className="message-row assistant-message"><div className="message-body"><div className="message-meta"><div className="message-avatar assistant-avatar"><Database size={16}/></div><strong>查询助手</strong></div><div className="loading-line"><span className="spinner"/>正在生成并执行查询，请稍候…</div></div></div>}</div>}<div ref={end}/></div>
    {!empty && composer}</div>
  </div>
}
