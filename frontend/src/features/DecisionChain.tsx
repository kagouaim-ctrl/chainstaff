import { ROLE_MAP } from '@/domain/agents'
import type { DecisionStep } from '@/domain/types'
import { clock } from '@/lib/format'
import { useDrawerLink } from '@/app/links'

/** 决策链：从 Scout 发现到主 Agent 决策的完整证据链。 */
export function DecisionChain({ steps }: { steps: DecisionStep[] }) {
  const open = useDrawerLink()
  return (
    <ol className="relative space-y-4 border-l border-line pl-5">
      {steps.map((s, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[25px] top-1 h-2.5 w-2.5 rounded-full border-2 border-panel bg-agent" />
          <div className="flex flex-wrap items-center gap-x-2 text-[13px]">
            <span className="font-medium">{s.title}</span>
            <button onClick={() => open('agent', s.agent)} className="text-agent hover:underline">{ROLE_MAP[s.agent].short}</button>
            <span className="num text-xs text-sub">{clock(s.at)}</span>
          </div>
          <p className="mt-1 text-[13px] text-fg/85">{s.detail}</p>
          {s.ref && <p className="mt-0.5 font-mono text-[11px] text-sub">{s.ref}</p>}
        </li>
      ))}
    </ol>
  )
}
