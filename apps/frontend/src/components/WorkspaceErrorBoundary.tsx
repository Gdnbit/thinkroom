import { Component, type ReactNode } from 'react'
import { ErrorState } from '@/components/ui'

export default class WorkspaceErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="workspace-page workspace-page--reading">
          <ErrorState message="页面加载失败" onRetry={() => window.location.reload()} />
        </div>
      )
    }
    return this.props.children
  }
}
