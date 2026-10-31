import { Link } from 'react-router-dom'
export function NotFound() {
  return <div className="py-24 text-center"><p className="text-lg font-semibold">页面不存在</p><Link to="/" className="mt-2 inline-block text-agent">回到总览</Link></div>
}
