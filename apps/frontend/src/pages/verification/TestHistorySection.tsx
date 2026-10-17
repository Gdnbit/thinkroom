/**
 * 可复用的测试历史记录区域组件
 * 在每个验证板块底部显示该板块的历史记录，并支持「沉浸式回放」。
 */
import { useState, useCallback, useEffect } from 'react'
import { History, Trash2, Eye, ChevronDown, ChevronRight, CheckCircle2, XCircle, Clipboard } from 'lucide-react'
import { clsx } from 'clsx'
import {
  filterHistory, deleteHistory, clearCategoryHistory, getStorageInfo,
  type TestHistoryRecord, type TestCategory,
} from '@/services/testHistory'

interface Props {
  category: TestCategory
  /** 当用户选择一条历史记录进入「沉浸式回放」时回调，传入记录（null 表示退出回放） */
  onReplay?: (record: TestHistoryRecord | null) => void
  /** 当前是否处于回放模式 */
  replayId?: string | null
}

function CopyBtn({ getText, label = '复制' }: { getText: () => string; label?: string }) {
  const [ok, setOk] = useState(false)
  return (
    <button onClick={() => { navigator.clipboard.writeText(getText()); setOk(true); setTimeout(() => setOk(false), 1500) }}
      className={clsx('flex items-center gap-1 px-2 py-1 rounded text-xs font-mono border transition-colors',
        ok ? 'border-emerald-400/50 text-emerald-400 bg-emerald-400/10' : 'border-border text-muted-foreground hover:text-foreground hover:bg-secondary')}>
      {ok ? <CheckCircle2 size={11} /> : <Clipboard size={11} />}{ok ? '已复制' : label}
    </button>
  )
}

const CAT_LABELS: Record<TestCategory, string> = {
  suite: '全链路验证', api: '接口面板', v2: 'V2 专项', ingestion: '解析质量', status: '系统状态', master: '终极检测',
}

export default function TestHistorySection({ category, onReplay, replayId }: Props) {
  const [records, setRecords] = useState<TestHistoryRecord[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [sectionOpen, setSectionOpen] = useState(false)

  const refresh = useCallback(() => {
    setRecords(filterHistory(category))
  }, [category])

  // 当 section 打开或 category 变更时刷新
  useEffect(() => { if (sectionOpen) refresh() }, [sectionOpen, refresh])

  const storageInfo = getStorageInfo()

  return (
    <div className="rounded-2xl border border-border bg-card overflow-hidden">
      <button onClick={() => setSectionOpen(o => !o)}
        className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-secondary/30 transition-colors">
        {sectionOpen ? <ChevronDown size={13} className="text-muted-foreground" /> : <ChevronRight size={13} className="text-muted-foreground" />}
        <History size={14} className="text-primary" />
        <span className="text-xs font-medium text-foreground">{CAT_LABELS[category]}历史记录</span>
        <span className="text-[10px] text-muted-foreground">（{records.length} 条 · {storageInfo.sizeKB}KB/{storageInfo.maxKB}KB）</span>
        {replayId && <span className="ml-auto text-[10px] px-2 py-0.5 rounded bg-amber-400/10 text-amber-400 border border-amber-400/30">回放模式</span>}
      </button>

      {sectionOpen && (
        <div className="border-t border-border px-4 py-3 space-y-2">
          {/* 工具栏 */}
          <div className="flex items-center gap-2 justify-end">
            {replayId && onReplay && (
              <button onClick={() => onReplay(null)}
                className="px-2 py-1 rounded text-xs text-amber-400 hover:bg-amber-400/10 border border-amber-400/30">
                退出回放
              </button>
            )}
            {records.length > 0 && (
              <button onClick={() => { if (confirm(`确定清空"${CAT_LABELS[category]}"的所有历史记录？`)) { clearCategoryHistory(category); refresh() } }}
                className="flex items-center gap-1 px-2 py-1 rounded text-xs text-red-400 hover:bg-red-400/10 border border-red-400/30">
                <Trash2 size={10} />清空本类
              </button>
            )}
          </div>

          {records.length === 0 ? (
            <div className="py-6 text-center">
              <History size={22} className="mx-auto text-muted-foreground mb-1 opacity-30" />
              <p className="text-xs text-muted-foreground">暂无历史记录</p>
            </div>
          ) : (
            <div className="space-y-1.5 max-h-[420px] overflow-auto">
              {records.map(rec => {
                const isExp = expanded === rec.id
                const isReplay = replayId === rec.id
                const passRate = rec.summary.total > 0 ? Math.round((rec.summary.passed / rec.summary.total) * 100) : 0
                const dt = new Date(rec.timestamp)
                const timeStr = `${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,'0')}-${String(dt.getDate()).padStart(2,'0')} ${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}`

                return (
                  <div key={rec.id} className={clsx('rounded-lg border transition-colors', isReplay ? 'border-amber-400/50 bg-amber-400/5' : isExp ? 'border-primary/40 bg-primary/5' : 'border-border bg-background')}>
                    <div className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-secondary/20" onClick={() => setExpanded(isExp ? null : rec.id)}>
                      <div className={clsx('w-1.5 h-1.5 rounded-full shrink-0', rec.summary.failed === 0 ? 'bg-emerald-400' : rec.summary.passed === 0 ? 'bg-red-400' : 'bg-amber-400')} />
                      <span className="text-[11px] text-muted-foreground">{timeStr}</span>
                      <span className="text-xs font-medium text-foreground truncate flex-1">{rec.label}</span>
                      <span className="text-[10px] text-emerald-400">✓{rec.summary.passed}</span>
                      {rec.summary.failed > 0 && <span className="text-[10px] text-red-400">✗{rec.summary.failed}</span>}
                      <span className="text-[10px] text-muted-foreground">{passRate}%</span>
                      {rec.durationMs > 0 && <span className="text-[10px] text-muted-foreground">{Math.round(rec.durationMs/1000)}s</span>}
                      <div className="flex items-center gap-0.5 shrink-0">
                        {onReplay && (
                          <button onClick={e => { e.stopPropagation(); onReplay(isReplay ? null : rec) }}
                            title={isReplay ? '退出回放' : '沉浸式回放'}
                            className={clsx('p-1 rounded', isReplay ? 'bg-amber-400/20 text-amber-400' : 'hover:bg-secondary text-muted-foreground hover:text-foreground')}>
                            <Eye size={11} />
                          </button>
                        )}
                        <button onClick={e => { e.stopPropagation(); deleteHistory(rec.id); refresh() }}
                          className="p-1 rounded hover:bg-red-400/10 text-muted-foreground hover:text-red-400">
                          <Trash2 size={11} />
                        </button>
                      </div>
                    </div>

                    {isExp && (
                      <div className="border-t border-border px-3 py-2 space-y-2">
                        {/* 场景明细（suite 类型） */}
                        {rec.details && Array.isArray(rec.details) && (
                          <div className="space-y-1">
                            {rec.details.map((res: any, idx: number) => (
                              <details key={idx} open={res.status !== 'passed'} className={clsx('rounded border p-1.5 text-[10px]', res.status === 'passed' ? 'border-emerald-400/20' : 'border-red-400/30')}>
                                <summary className="cursor-pointer flex items-center gap-1.5">
                                  {res.status === 'passed' ? <CheckCircle2 size={10} className="text-emerald-400" /> : <XCircle size={10} className="text-red-400" />}
                                  <span className="text-foreground">{res.scenario_name || res.label || `项目 ${idx+1}`}</span>
                                  <span className="text-muted-foreground ml-auto">{res.duration_ms ?? res.ms ?? 0}ms</span>
                                </summary>
                                <div className="mt-1 pl-3 space-y-0.5 text-muted-foreground">
                                  {(res.assertions || []).map((a: any, ai: number) => (
                                    <div key={ai}>{a.status === 'passed' ? '✓' : '✗'} {a.name}</div>
                                  ))}
                                  {res.detail && <div>{res.detail}</div>}
                                </div>
                              </details>
                            ))}
                          </div>
                        )}
                        {/* 非数组 details */}
                        {rec.details && !Array.isArray(rec.details) && typeof rec.details === 'object' && (
                          <pre className="text-[10px] font-mono text-muted-foreground max-h-40 overflow-auto whitespace-pre-wrap">
                            {JSON.stringify(rec.details, null, 2).slice(0, 3000)}
                          </pre>
                        )}
                        {/* 日志 */}
                        {rec.logText && (
                          <details className="rounded border border-border">
                            <summary className="cursor-pointer text-[10px] p-1.5 text-muted-foreground">运行日志</summary>
                            <pre className="p-1.5 text-[10px] font-mono text-muted-foreground max-h-48 overflow-auto whitespace-pre-wrap">{rec.logText}</pre>
                          </details>
                        )}
                        <CopyBtn getText={() => {
                          const lines = [`=== ${rec.label} ===`, `时间: ${timeStr}`, `通过: ${rec.summary.passed}/${rec.summary.total}`, '']
                          if (rec.logText) lines.push(rec.logText)
                          return lines.join('\n')
                        }} label="复制此次结果" />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
