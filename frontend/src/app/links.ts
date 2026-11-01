import { useSearchParams } from 'react-router-dom'

/** 打开抽屉：保持当前页面，只改 URL 参数，所以抽屉可以通过 URL 直接访问。 */
export function useDrawerLink() {
  const [params, setParams] = useSearchParams()
  return (key: 'agent' | 'order', value: string) => {
    const p = new URLSearchParams(params)
    p.set(key, value)
    setParams(p)
  }
}
