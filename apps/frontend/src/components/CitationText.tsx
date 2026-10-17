import { Children, ReactNode, isValidElement, cloneElement } from 'react'
import CitationChip, { CitationData } from './CitationChip'

/**
 * 在 ReactMarkdown 渲染的节点树中，递归扫描文本节点，
 * 将形如 [1]、[2]、[3][5] 的引用标记替换为 <CitationChip />。
 *
 * 设计要点：
 *  - 不破坏 Markdown 结构（粗体 / 列表 / 链接 等仍正常）
 *  - 只替换已注册的 citation index（越界编号原样保留）
 *  - 支持连续多引用 [1][2][3]
 */
const CITATION_REGEX = /\[(\d+)\]/g

function renderTextWithCitations(text: string, map: Map<number, CitationData>): ReactNode[] {
  const parts: ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  let key = 0
  CITATION_REGEX.lastIndex = 0
  while ((match = CITATION_REGEX.exec(text)) !== null) {
    const idx = Number(match[1])
    const cit = map.get(idx)
    if (!cit) continue  // 未注册编号：原样保留（由 slice 携带出去）
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index))
    }
    parts.push(<CitationChip key={`c-${key++}`} citation={cit} />)
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }
  // 若从头到尾都没命中任何已注册编号，返回原串
  return parts.length === 0 ? [text] : parts
}

export function withCitations(children: ReactNode, citations: CitationData[]): ReactNode {
  if (!citations || citations.length === 0) return children
  const map = new Map(citations.map(c => [c.index, c]))

  const walk = (node: ReactNode): ReactNode => {
    if (typeof node === 'string') {
      const replaced = renderTextWithCitations(node, map)
      return replaced.length === 1 ? replaced[0] : <>{replaced.map((n, i) => <span key={i}>{n}</span>)}</>
    }
    if (Array.isArray(node)) {
      return node.map((n, i) => <span key={i}>{walk(n)}</span>)
    }
    if (isValidElement(node)) {
      const el = node as any
      const inner = el.props?.children
      if (inner == null) return node
      const newChildren = Children.map(inner, walk)
      return cloneElement(node, el.props, newChildren)
    }
    return node
  }

  return Children.map(children, walk)
}
