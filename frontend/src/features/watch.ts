import { useQueries } from '@tanstack/react-query'
import { useApp } from '@/app/store'
import { source } from '@/api/source'
import type { Token } from '@/domain/types'

/**
 * 自选代币：列表里有的直接用，不在当前列表里的（比如从新币页、搜索加的）单独查。
 * 这样自选不会因为代币掉出热门榜就消失。
 */
export function useWatched(list: Token[] | undefined): Token[] {
  const watch = useApp((s) => s.watch)
  const have = new Map((list ?? []).map((t) => [t.address, t]))
  const ids = Object.keys(watch).filter((a) => watch[a])
  const missing = list ? ids.filter((a) => !have.has(a)) : []
  const extra = useQueries({ queries: missing.map((a) => ({ queryKey: ['token', a], queryFn: () => source().token(a), staleTime: 60_000 })) })
  const fromList = (list ?? []).filter((t) => watch[t.address] ?? t.watch)
  return [...fromList, ...extra.map((q) => q.data).filter((t): t is Token => !!t)]
}
