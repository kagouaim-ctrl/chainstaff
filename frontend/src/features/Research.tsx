import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { create } from 'zustand'
import { isDemo } from '@/api/hooks'
import { AGENTS_ONLINE, startRun, watchRun } from '@/api/live'
import { useToast } from '@/app/store'
import { Button, Dialog } from '@/components/ui'

const CODE_KEY = 'meme-access-code'
const readCode = () => { try { return sessionStorage.getItem(CODE_KEY) ?? '' } catch { return '' } }
const saveCode = (v: string) => { try { if (v) sessionStorage.setItem(CODE_KEY, v); else sessionStorage.removeItem(CODE_KEY) } catch { /* 隐私模式：这次会话不记住 */ } }

interface Target { address: string; symbol: string }
const useTarget = create<{ target: Target | null; set: (t: Target | null) => void }>((set) => ({ target: null, set: (target) => set({ target }) }))

/** 把代币交给 Agent 研究。 */
export function useResearch() {
  const toast = useToast((s) => s.show)
  const set = useTarget((s) => s.set)
  return (t: Target) => {
    if (isDemo()) toast(`演示模式：没有为 ${t.symbol} 创建研究任务。`)
    else if (!AGENTS_ONLINE) toast(`Agent 分析还没上线：没有为 ${t.symbol} 创建研究任务。`)
    else set(t)
  }
}

/** 挂在页面外层：要访问码、发起分析，并在 Agent 发言时刷新页面。 */
export function ResearchRunner() {
  const { target, set } = useTarget()
  const toast = useToast((s) => s.show)
  const qc = useQueryClient()
  const [code, setCode] = useState('')
  const [asking, setAsking] = useState(false)

  const start = async (t: Target, access: string) => {
    try {
      const { id } = await startRun(t.address, access)
      saveCode(access)
      setAsking(false)
      set(null)
      toast(`Agent 开始研究 ${t.symbol}，一般要一到几分钟。讨论会出现在代币群里。`)
      const refresh = () => {
        for (const key of ['chat', 'theses', 'research']) qc.invalidateQueries({ queryKey: [key, t.address] })
        qc.invalidateQueries({ queryKey: ['token', t.address] })
        qc.invalidateQueries({ queryKey: ['tokens'] })
        qc.invalidateQueries({ queryKey: ['events'] })
        qc.invalidateQueries({ queryKey: ['agents'] })
      }
      refresh()
      watchRun(id, refresh, (status) => {
        refresh()
        if (status === 'failed') toast(`${t.symbol} 的分析没跑完。`)
        else if (status === 'lost') toast('和后端的连接断了，刷新页面查看结果。')
      })
    } catch (e) {
      if (e instanceof Error && e.message === '访问码不对') { saveCode(''); setCode(''); setAsking(true) }
      else set(null)
      toast(e instanceof Error ? e.message : '没能开始分析')
    }
  }

  useEffect(() => {
    if (!target) return
    const saved = readCode()
    if (saved) void start(target, saved)
    else setAsking(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 每选一个代币只发起一次
  }, [target])

  const close = () => { setAsking(false); set(null) }
  return (
    <Dialog open={asking && !!target} onClose={close} title={`让 Agent 研究 ${target?.symbol ?? ''}`} footer={<>
      <Button onClick={close}>取消</Button>
      <Button variant="primary" onClick={() => { if (target && code.trim()) void start(target, code.trim()) }}>开始研究</Button>
    </>}>
      <p className="text-sub">一次分析会调用很多次模型，所以需要访问码。分析结果所有人都能看。这里只出分析和决定，不会下单。</p>
      <label className="mt-3 block text-[13px]">访问码
        <input type="password" autoComplete="off" autoFocus value={code} onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && target && code.trim()) void start(target, code.trim()) }}
          className="mt-1 h-10 w-full rounded-lg border border-line bg-bg px-3 text-[14px] outline-none focus:border-agent" />
      </label>
    </Dialog>
  )
}
