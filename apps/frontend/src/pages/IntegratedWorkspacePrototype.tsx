import { useMemo, useState } from 'react'
import {
  Archive,
  BarChart3,
  BookOpen,
  Brain,
  CheckCircle2,
  CircleAlert,
  ClipboardCheck,
  Database,
  FileText,
  FlaskConical,
  Gauge,
  MessageSquare,
  Paperclip,
  Save,
  Settings,
  UploadCloud,
} from 'lucide-react'
import { clsx } from 'clsx'

type WorkspaceView = 'chat' | 'materials' | 'records' | 'data' | 'advanced'

const views: Array<{
  id: WorkspaceView
  label: string
  desc: string
  badge: string
  icon: React.ElementType
}> = [
  { id: 'chat', label: '决策问答', desc: '上传、提问、审慎回答', badge: '主', icon: MessageSquare },
  { id: 'materials', label: '资料确认', desc: '知识库、审计、来源画像', badge: '4', icon: BookOpen },
  { id: 'records', label: '决策记录', desc: '快照、复盘、审议记录', badge: '3', icon: Brain },
  { id: 'data', label: '数据状态', desc: '数据源、基金、质量、成本', badge: '6', icon: Database },
  { id: 'advanced', label: '高级维护', desc: '验证、评估、设置、修复', badge: '管', icon: Settings },
]

const viewCopy: Record<WorkspaceView, { title: string; subtitle: string }> = {
  chat: {
    title: '决策问答',
    subtitle: '拖入资料或直接提问，系统在后台完成解析、引用、审议和风险评估',
  },
  materials: {
    title: '资料确认工作区',
    subtitle: '知识库、入库审计、来源画像和质量状态从资料卡片进入',
  },
  records: {
    title: '决策记录工作区',
    subtitle: '快照、审议记录、复盘和 outcome 统一收纳',
  },
  data: {
    title: '数据状态工作区',
    subtitle: '数据源、基金估值、质量监控和成本只作为状态支撑',
  },
  advanced: {
    title: '高级维护工作区',
    subtitle: '验证、评估、模型、Key、社媒和数据修复默认高级化',
  },
}

const integrationColumns = [
  {
    title: '问答主界面',
    icon: MessageSquare,
    modules: [
      ['ResearchChat', '会话、消息、自动路由、上传入口'],
      ['Briefings', '作为回答中的研究摘要能力'],
      ['Committee', '折叠为审议过程，不做一级页'],
    ],
  },
  {
    title: '资料确认',
    icon: ClipboardCheck,
    modules: [
      ['KnowledgeBase', '资料状态、图谱、分类、引用'],
      ['IngestionAudit', '处理痕迹、全文、claims'],
      ['Speakers', '来源画像和可信度'],
    ],
  },
  {
    title: '决策记录',
    icon: Archive,
    modules: [
      ['DecisionSnapshots', '证据冻结、回放、PnL'],
      ['Review', 'outcome 与事后验证'],
      ['Overrides', '用户修正和采纳反馈'],
    ],
  },
  {
    title: '数据状态',
    icon: BarChart3,
    modules: [
      ['DataHub', '数据源、额度、提醒'],
      ['Fund', '估值、搜索、历史'],
      ['QualityMonitor', '健康灯和异常详情'],
    ],
  },
  {
    title: '高级维护',
    icon: FlaskConical,
    modules: [
      ['Verification', '场景、API、模式路由、模型 ping'],
      ['Settings', '模型、Key、Cookie、社媒'],
      ['DataRepair', '默认隐藏，需确认和审计'],
    ],
  },
]

const stages = [
  ['接收', '文件持久化保存', 'done'],
  ['解析', '正文、表格、元数据', 'done'],
  ['切片', '证据单元与观点卡', 'done'],
  ['入库', '索引与向量存储', 'active'],
  ['可用', '可被回答引用', 'idle'],
] as const

function Chip({
  children,
  tone = 'default',
}: {
  children: React.ReactNode
  tone?: 'default' | 'ok' | 'warn' | 'danger'
}) {
  return (
    <span
      className={clsx(
        'inline-flex min-h-7 items-center gap-1.5 rounded-lg border bg-card px-2 py-1 text-[11px]',
        tone === 'default' && 'border-border text-foreground/70',
        tone === 'ok' && 'border-emerald-400/30 text-emerald-300',
        tone === 'warn' && 'border-amber-400/30 text-amber-300',
        tone === 'danger' && 'border-red-400/30 text-red-300',
      )}
    >
      {children}
    </span>
  )
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string
  value: React.ReactNode
  tone?: 'ok' | 'warn' | 'danger'
}) {
  return (
    <div className="flex min-h-7 items-center justify-between gap-3 border-t border-border-subtle pt-2 first:border-t-0 first:pt-0">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      {tone ? <Chip tone={tone}>{value}</Chip> : <strong className="text-[11px] font-semibold text-foreground">{value}</strong>}
    </div>
  )
}

function SectionCard({
  title,
  badge,
  children,
}: {
  title: string
  badge?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="rounded-xl border border-border bg-card p-3">
      <header className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-xs font-semibold text-foreground">{title}</h3>
        {badge}
      </header>
      <div className="space-y-2">{children}</div>
    </section>
  )
}

export default function IntegratedWorkspacePrototype() {
  const [activeView, setActiveView] = useState<WorkspaceView>('chat')
  const activeCopy = viewCopy[activeView]

  const viewActions = useMemo(
    () => [
      { label: '打开迁移表', icon: FileText },
      { label: '保存快照', icon: Save, primary: true },
    ],
    [],
  )

  return (
    <div className="min-h-[calc(100vh-2rem)] space-y-4">
      <div className="rounded-xl border border-border bg-card/70">
        <div className="flex flex-col gap-3 border-b border-border-subtle px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.2em] text-primary">Frontend prototype</p>
            <h1 className="text-xl font-semibold tracking-tight text-foreground">{activeCopy.title}</h1>
            <p className="mt-1 text-xs text-muted-foreground">{activeCopy.subtitle}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Chip tone="ok">
              <CheckCircle2 size={13} />
              知识库模拟在线
            </Chip>
            <Chip tone="warn">
              <CircleAlert size={13} />
              未接后端
            </Chip>
            {viewActions.map((action) => {
              const Icon = action.icon
              return (
                <button
                  key={action.label}
                  className={clsx(
                    'inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs font-semibold',
                    action.primary
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-border bg-secondary text-foreground hover:bg-surface-3',
                  )}
                >
                  <Icon size={13} />
                  {action.label}
                </button>
              )
            })}
          </div>
        </div>

        <div className="grid gap-3 p-3 lg:grid-cols-[260px_minmax(0,1fr)]">
          <aside className="space-y-3">
            <div className="rounded-xl border border-border bg-background/40 p-2">
              <p className="px-2 pb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">Primary Workflow</p>
              <div className="space-y-1.5">
                {views.map((view) => {
                  const Icon = view.icon
                  return (
                    <button
                      key={view.id}
                      onClick={() => setActiveView(view.id)}
                      className={clsx(
                        'grid w-full grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-2 rounded-lg border p-2 text-left transition',
                        activeView === view.id
                          ? 'border-border bg-secondary text-foreground'
                          : 'border-transparent text-muted-foreground hover:border-border hover:bg-secondary/60 hover:text-foreground',
                      )}
                    >
                      <span className="grid h-8 w-8 place-items-center rounded-lg border border-border bg-card text-primary">
                        <Icon size={15} />
                      </span>
                      <span className="min-w-0">
                        <strong className="block truncate text-xs font-semibold">{view.label}</strong>
                        <span className="block truncate text-[10px] text-muted-foreground">{view.desc}</span>
                      </span>
                      <span className="rounded-full border border-border bg-background/50 px-2 py-0.5 text-[10px] text-muted-foreground">{view.badge}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="rounded-xl border border-border bg-background/40 p-3">
              <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">Current Pages Mapped</p>
              <div className="space-y-2">
                <Metric label="旧 Dashboard" value="总览入口" />
                <Metric label="策略与回测" value="回答后验证" />
                <Metric label="基金配置与核对" value="高级收纳" />
                <Metric label="原有路由" value="全部保留" tone="ok" />
              </div>
            </div>
          </aside>

          <section className="grid min-w-0 gap-3 xl:grid-cols-[minmax(0,1fr)_340px]">
            <div className="min-w-0 space-y-3">
              <section className="overflow-hidden rounded-xl border border-border bg-card">
                <div className="flex flex-col gap-3 border-b border-border-subtle bg-secondary/40 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
                  <h2 className="text-sm font-semibold text-foreground">主流程：资料 + 问答 + 决策</h2>
                  <div className="inline-flex w-fit gap-1 rounded-lg border border-border bg-background/50 p-1">
                    {['自动', '仅资料', '外部数据', '审议'].map((mode, index) => (
                      <button
                        key={mode}
                        className={clsx(
                          'h-7 rounded-md px-2.5 text-[11px]',
                          index === 0 ? 'bg-primary text-primary-foreground font-semibold' : 'text-muted-foreground hover:bg-secondary',
                        )}
                      >
                        {mode}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="max-h-[calc(100vh-19rem)] min-h-[31rem] space-y-3 overflow-auto p-4">
                  <article className="ml-auto w-full rounded-xl border border-primary/30 bg-primary/5 p-3 lg:w-[86%]">
                    <div className="mb-2 flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
                      <span>用户输入</span>
                      <span>3 个附件</span>
                    </div>
                    <p className="text-sm leading-relaxed text-foreground/80">
                      请基于这些资料，判断当前是否适合继续加仓；如果仅依据我上传的文件，给出操作逻辑和关键风险。
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {['Q1_report.pdf', 'manager_call.md', 'fund_position.xlsx'].map((file) => (
                        <Chip key={file}>
                          <Paperclip size={12} />
                          {file}
                        </Chip>
                      ))}
                    </div>
                  </article>

                  <article className="rounded-xl border border-border bg-secondary/30 p-3">
                    <div className="rounded-xl border border-border bg-background/30 p-3">
                      <div className="mb-3 flex items-start justify-between gap-3">
                        <div>
                          <h3 className="text-xs font-semibold text-foreground">资料处理链路</h3>
                          <p className="mt-1 text-[11px] text-muted-foreground">来自上传、知识库、入库审计、来源画像、质量监控的合并状态</p>
                        </div>
                        <button className="text-[11px] font-semibold text-primary">资料详情</button>
                      </div>
                      <div className="grid gap-2 md:grid-cols-5">
                        {stages.map(([title, desc, state]) => (
                          <div
                            key={title}
                            className={clsx(
                              'min-h-16 rounded-lg border p-2',
                              state === 'done' && 'border-emerald-400/30 bg-emerald-400/5',
                              state === 'active' && 'border-primary/50 bg-primary/10',
                              state === 'idle' && 'border-border bg-card',
                            )}
                          >
                            <strong className="block text-[11px] text-foreground">{title}</strong>
                            <span className="mt-1 block text-[10px] leading-snug text-muted-foreground">{desc}</span>
                          </div>
                        ))}
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Chip tone="ok">42 条观点</Chip>
                        <Chip tone="ok">12 条引用</Chip>
                        <Chip tone="warn">2 条冲突</Chip>
                        <Chip>来源可信度 B+</Chip>
                      </div>
                    </div>
                  </article>

                  <article className="rounded-xl border border-border bg-secondary/30 p-3">
                    <div className="mb-2 flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
                      <span>系统回答</span>
                      <span>范围：仅基于上传资料，未调用外部实时数据</span>
                    </div>
                    <div className="border-l-2 border-primary pl-3 text-sm leading-relaxed text-foreground">
                      当前更适合维持观察或小幅分批，而不是一次性加仓。上传资料支持“基本面未恶化”，但没有充分证明“估值已经具备高安全边际”。如果只依据这些文件，系统建议把动作限制在小比例验证和条件触发，而不是提高核心仓位。
                    </div>
                    <div className="mt-3 grid gap-2 lg:grid-cols-4">
                      {[
                        ['结论', '谨慎观察，允许小比例分批，不建议一次性加仓。'],
                        ['依据', '电话会、持仓表和季度资料互相支持，但缺少最新市场确认。'],
                        ['风险', '估值假设偏乐观；资金流未验证；单一来源偏多。'],
                        ['下一步', '补充外部行情与同业数据；保存快照后进入复盘。'],
                      ].map(([title, desc]) => (
                        <div key={title} className="rounded-lg border border-border bg-card p-3">
                          <h3 className="text-xs font-semibold text-foreground">{title}</h3>
                          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{desc}</p>
                        </div>
                      ))}
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Chip tone="ok">证据可展开</Chip>
                      <Chip tone="warn">审议过程折叠</Chip>
                      <Chip>可保存为决策快照</Chip>
                    </div>
                  </article>
                </div>

                <footer className="border-t border-border-subtle bg-secondary/30 p-3">
                  <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-end gap-3 rounded-xl border border-border bg-background/40 p-3">
                    <button className="grid h-10 w-10 place-items-center rounded-lg border border-border bg-card text-foreground" title="添加资料">
                      <UploadCloud size={16} />
                    </button>
                    <textarea
                      className="min-h-10 max-h-28 resize-y bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
                      placeholder="拖入资料或输入问题。只上传资料时，系统返回摘要、主题、入库状态和可追问方向。"
                    />
                    <button className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary px-4 text-xs font-semibold text-primary-foreground">
                      <MessageSquare size={14} />
                      发送
                    </button>
                  </div>
                  <div className="mt-2 flex justify-between gap-3 text-[10px] text-muted-foreground">
                    <span>上传入口合并到输入框；不再单独放复杂投料页</span>
                    <span>Enter 发送，Shift+Enter 换行</span>
                  </div>
                </footer>
              </section>

              <section className="rounded-xl border border-border bg-card">
                <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
                  <h2 className="text-sm font-semibold text-foreground">现有功能整合安排</h2>
                  <button className="text-[11px] font-semibold text-primary">打开完整迁移矩阵</button>
                </div>
                <div className="grid gap-2 p-3 xl:grid-cols-5">
                  {integrationColumns.map((column) => {
                    const Icon = column.icon
                    return (
                      <div key={column.title} className="rounded-xl border border-border bg-background/40 p-3">
                        <div className="mb-3 flex items-center gap-2">
                          <Icon size={14} className="text-primary" />
                          <h3 className="text-xs font-semibold text-foreground">{column.title}</h3>
                        </div>
                        <div className="space-y-2">
                          {column.modules.map(([name, desc]) => (
                            <div key={name} className="rounded-lg border border-border-subtle bg-card p-2">
                              <strong className="block text-[11px] text-foreground">{name}</strong>
                              <span className="mt-1 block text-[10px] leading-snug text-muted-foreground">{desc}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </section>
            </div>

            <aside className="space-y-3">
              <SectionCard title="资料状态" badge={<Chip tone="ok">可引用</Chip>}>
                <Metric label="已保存文件" value="3" />
                <Metric label="解析成功" value="3" />
                <Metric label="观点卡" value="42" />
                <Metric label="冲突信息" value="2" />
                <button className="text-left text-[11px] font-semibold text-primary">打开资料确认</button>
              </SectionCard>

              <SectionCard title="数据状态" badge={<Chip tone="warn">未用于本回答</Chip>}>
                <Metric label="数据源" value="9/11 正常" />
                <Metric label="基金估值" value="可用" />
                <Metric label="质量监控" value="无阻塞" />
                <Metric label="成本预算" value="正常" />
                <button className="text-left text-[11px] font-semibold text-primary">查看数据状态</button>
              </SectionCard>

              <SectionCard title="回答透明度" badge={<Chip tone="ok">可追溯</Chip>}>
                <Metric label="资料内事实" value="12" />
                <Metric label="系统推断" value="4" />
                <Metric label="风险项" value="3" />
                <Metric label="待补充" value="3" />
              </SectionCard>

              <SectionCard title="决策闭环" badge={<Chip tone="warn">未保存</Chip>}>
                <Metric label="快照" value="待保存" />
                <Metric label="审议记录" value="已生成" />
                <Metric label="复盘任务" value="未创建" />
                <button className="inline-flex h-8 items-center justify-center rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground">保存并进入记录</button>
              </SectionCard>

              <SectionCard title="高级入口" badge={<Chip>收纳</Chip>}>
                <Metric label="验证中心" value={<FlaskConical size={14} />} />
                <Metric label="评估实验室" value={<Gauge size={14} />} />
                <Metric label="系统设置" value={<Settings size={14} />} />
                <Metric label="数据修复" value="隐藏" tone="danger" />
              </SectionCard>
            </aside>
          </section>
        </div>
      </div>
    </div>
  )
}
