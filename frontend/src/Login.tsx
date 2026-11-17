import { useState, type FormEvent } from 'react'
import { ArrowRight, Database } from 'lucide-react'
import { post } from './api'
import type { User } from './types'
export function Login({onLogin}: {onLogin: (user: User) => void}) {
  const [username, setUsername] = useState(''); const [password, setPassword] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  async function submit(e: FormEvent) { e.preventDefault(); setError(''); setBusy(true); try { onLogin(await post<User>('/api/login', {username, password})) } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  return <div className="login-layout"><section className="login-story"><div className="brand brand-inverse"><div className="brand-icon"><Database size={19}/></div><div><strong>TSChat</strong><span>自然语言数据查询</span></div></div><div className="login-intro"><h1>让数据回答<br/>你的问题。</h1><p>从自然语言到数据库查询，<br/>让数据探索更简单。</p></div></section><main className="login-main"><div className="login-form"><div className="mobile-brand"><Database size={20}/> TSChat</div><h2>欢迎回来</h2><p>登录后开始探索你的数据。</p><form onSubmit={submit}><label htmlFor="username">用户名</label><input id="username" autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} required placeholder="请输入用户名"/><label htmlFor="password">密码</label><input id="password" type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required placeholder="请输入密码"/>{error && <div className="form-error" role="alert">{error}</div>}<button className="primary login-submit" disabled={busy}>{busy ? '正在登录…' : '登录'}<ArrowRight size={18}/></button></form><div className="login-help">使用管理员为你创建的账号登录。</div></div></main></div>
}
