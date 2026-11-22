import { useLayoutEffect, useRef, type ReactNode } from 'react'
import { clsx } from 'clsx'

export default function WorkspaceDialog({ title, children, onClose, drawer = false }: {
  title: string
  children: ReactNode
  onClose: () => void
  drawer?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)

  useLayoutEffect(() => {
    const dialog = ref.current
    const trigger = document.activeElement
    dialog?.showModal()
    return () => {
      dialog?.close()
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus()
    }
  }, [])

  return (
    <dialog
      ref={ref}
      aria-label={title}
      className={clsx('workspace-dialog', drawer && 'workspace-dialog--drawer')}
      onCancel={(event) => {
        event.preventDefault()
        event.currentTarget.close()
        onClose()
      }}
    >
      {children}
    </dialog>
  )
}
