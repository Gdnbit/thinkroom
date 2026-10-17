import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from '@/components/Layout'
import Dashboard from '@/pages/Dashboard'
import DecisionChatFlow from '@/pages/DecisionChatFlow'

const Watchlist = lazy(() => import('@/pages/Watchlist'))
const ResearchChat = lazy(() => import('@/pages/ResearchChat'))
const Strategies = lazy(() => import('@/pages/Strategies'))
const Review = lazy(() => import('@/pages/Review'))
const Verification = lazy(() => import('@/pages/Verification'))
const Settings = lazy(() => import('@/pages/Settings'))
const KnowledgeBase = lazy(() => import('@/pages/KnowledgeBase'))
const DebateViewer = lazy(() => import('@/pages/DebateViewer'))
const DataFlowPage = lazy(() => import('@/pages/DataFlow'))
const DataHub = lazy(() => import('@/pages/DataHub'))
const Backtest = lazy(() => import('@/pages/Backtest'))
const IngestionAudit = lazy(() => import('@/pages/IngestionAudit'))
const Speakers = lazy(() => import('@/pages/Speakers'))
const QualityMonitor = lazy(() => import('@/pages/QualityMonitor'))
const FundReconciliation = lazy(() => import('@/pages/FundReconciliation'))
const FundProfiles = lazy(() => import('@/pages/FundProfiles'))
const QualityEvaluator = lazy(() => import('@/pages/QualityEvaluator'))

function PageFallback() {
  return (
    <div className="flex min-h-[40vh] items-center justify-center text-xs text-muted-foreground">
      Loading
    </div>
  )
}

export default function App() {
  return (
    <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="legacy-dashboard" element={<Dashboard />} />
          <Route path="inbox" element={<Navigate to="/knowledge" replace />} />
          <Route path="watchlist" element={<Watchlist />} />
          <Route path="chat" element={<DecisionChatFlow />} />
          <Route path="legacy-chat" element={<ResearchChat />} />
          <Route path="strategies" element={<Strategies />} />
          <Route path="review" element={<Review />} />
          <Route path="debate" element={<DebateViewer />} />
          <Route path="verification" element={<Verification />} />
          <Route path="verification/dataflow" element={<DataFlowPage />} />
          <Route path="settings" element={<Settings />} />
          <Route path="knowledge" element={<KnowledgeBase />} />
          <Route path="datahub" element={<DataHub />} />
          <Route path="backtest" element={<Backtest />} />
          <Route path="audit" element={<IngestionAudit />} />
          <Route path="speakers" element={<Speakers />} />
          <Route path="quality-monitor" element={<QualityMonitor />} />
          <Route path="fund-reconciliation" element={<FundReconciliation />} />
          <Route path="fund-profiles" element={<FundProfiles />} />
          <Route path="decision-snapshots" element={<Navigate to="/review" replace />} />
          <Route path="quality-evaluator" element={<QualityEvaluator />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
