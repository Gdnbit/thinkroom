/**
 * N5 · a11y · useFocusTrap
 *
 * Modal / drawer 专用：在容器内 Tab 循环（最后一个 Tab → 第一个；Shift+Tab 反向）。
 * 容器消失时自动 return focus 到打开它的元素（last active）.
 *
 * 用法：
 * ```tsx
 * const ref = useRef<HTMLDivElement>(null)
 * useFocusTrap(ref, isOpen)
 * return isOpen ? <div ref={ref} role="dialog" aria-modal="true">...</div> : null
 * ```
 *
 * 设计：
 * - 不引入 focus-trap 库（~5KB），手写一份 50 行实现核心场景
 * - 入口聚焦：优先聚焦到 [data-autofocus]，否则容器内第一个可聚焦元素，否则容器自身
 * - return focus 用 isFinite 元素引用兜底（lastActive 可能已消失）
 */
import { useEffect } from 'react'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function getFocusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    .filter(el => !el.hasAttribute('aria-hidden') && el.offsetParent !== null)
}

export function useFocusTrap(
  ref: React.RefObject<HTMLElement>,
  active: boolean,
): void {
  useEffect(() => {
    if (!active || !ref.current) return
    const container = ref.current
    const lastActive = document.activeElement as HTMLElement | null

    // 入口聚焦
    const autofocus = container.querySelector<HTMLElement>('[data-autofocus]')
    const focusables = getFocusable(container)
    const first = autofocus ?? focusables[0] ?? container
    first.focus()

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const items = getFocusable(container)
      if (items.length === 0) {
        e.preventDefault()
        return
      }
      const firstEl = items[0]
      const lastEl = items[items.length - 1]
      const activeEl = document.activeElement as HTMLElement | null

      if (e.shiftKey) {
        // Shift+Tab：第一个 → 最后一个
        if (activeEl === firstEl || !container.contains(activeEl)) {
          e.preventDefault()
          lastEl.focus()
        }
      } else {
        // Tab：最后一个 → 第一个
        if (activeEl === lastEl) {
          e.preventDefault()
          firstEl.focus()
        }
      }
    }

    container.addEventListener('keydown', onKeyDown)
    return () => {
      container.removeEventListener('keydown', onKeyDown)
      // return focus 到打开 modal 的元素
      if (lastActive && document.contains(lastActive)) {
        lastActive.focus()
      }
    }
  }, [ref, active])
}
