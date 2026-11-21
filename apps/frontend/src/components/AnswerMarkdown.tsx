import type { ComponentProps } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function AnswerMarkdown(props: ComponentProps<typeof ReactMarkdown>) {
  return <ReactMarkdown {...props} remarkPlugins={[remarkGfm]} />
}
