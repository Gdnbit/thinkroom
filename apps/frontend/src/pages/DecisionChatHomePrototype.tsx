import {
  Archive,
  BookOpen,
  CheckCircle2,
  CircleAlert,
  ClipboardCheck,
  Database,
  FileText,
  FlaskConical,
  MessageSquare,
  Paperclip,
  Save,
  Search,
  ShieldCheck,
  UploadCloud,
} from 'lucide-react'
import { clsx } from 'clsx'

const stages = [
  ['接收', '文件已保存', 'done'],
  ['解析', '正文和表格', 'done'],
  ['切片', '证据单元', 'done'],
  ['入库', '索引与向量', 'active'],
  ['可用', '回答可引用', 'idle'],
] as const

const sourceCards = [
  ['资料确认', '知识库、状态、引用', BookOpen],
  ['入库审计', '全文、claims、trace', ClipboardCheck],
  ['决策记录', '快照、复盘、审议', Archive],
  ['数据状态', '数据源、基金、质量', Database],
  ['高级维护', '验证、评估、设置', FlaskConical],
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

function SideMetric({
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

function SideCard({
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
        <h2 className="text-xs font-semibold text-foreground">{title}</h2>
        {badge}
      </header>
      <div className="space-y-2">{children}</div>
    </section>
  )
}

function AssistantAnswer() {
  const blocks = [
    ['结论', '更适合维持观察或小幅分批，不建议一次性加仓。'],
    ['依据', '电话会、持仓表和季度资料互相支持，但缺少最新市场确认。'],
    ['风险', '估值假设偏乐观；资金流未验证；单一来源观点偏多。'],
    ['下一步', '补充外部行情与同业数据；保存快照后进入复盘。'],
  ]

  return (
    <article className="rounded-xl border border-border bg-secondary/30 p-3">
      <div className="mb-2 flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
        <span>系统回答</span>
        <span>仅基于上传资料，未调用外部实时数据</span>
      </div>
      <div className="border-l-2 border-primary pl-3 text-sm leading-relaxed text-foreground">
        当前更适合维持观察或小幅分批，而不是一次性加仓。上传资料支持“基本面未恶化”，但没有充分证明“估值已经具备高安全边际”。如果只依据这些文件，系统建议把动作限制在小比例验证和条件触发，而不是提高核心仓位。
      </div>
      <div className="mt-3 grid gap-2 lg:grid-cols-4">
        {blocks.map(([title, desc]) => (
          <div key={title} className="rounded-lg border border-border bg-card p-3">
            <h3 className="text-xs font-semibold text-foreground">{title}</h3>
            <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{desc}</p>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Chip tone="ok">12 条证据</Chip>
        <Chip tone="warn">2 条冲突</Chip>
        <Chip>3 项信息缺口</Chip>
        <Chip>可保存为快照</Chip>
      </div>
    </article>
  )
}

export default function DecisionChatHomePrototype() {
  return (
    <div className="flex min-h-full flex-col bg-background">
      <header className="border-b border-border bg-background/80 px-5 py-4 backdrop-blur">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.22em] text-primary">Decision Chat</p>
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">决策问答</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              拖入资料或直接提问，系统在后台完成解析、引用、审议和风险评估。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Chip tone="ok">
              <CheckCircle2 size={13} />
              知识库模拟在线
            </Chip>
            <Chip tone="warn">
              <CircleAlert size={13} />
              前端原型，未接后端
            </Chip>
            <button className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-secondary px-3 text-xs font-semibold text-foreground">
              <FileText size={13} />
              查看迁移表
            </button>
            <button className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground">
              <Save size={13} />
              保存快照
            </button>
          </div>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-border bg-card">
          <div className="flex flex-col gap-3 border-b border-border-subtle bg-secondary/30 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-center gap-2">
              <MessageSquare size={16} className="text-primary" />
              <h2 className="text-sm font-semibold text-foreground">问答主线</h2>
            </div>
            <div className="inline-flex w-fit gap-1 rounded-lg border border-border bg-background/50 p-1">
              {['自动', '仅资料', '外部数据', '审议'].map((mode, index) => (
                <button
                  key={mode}
                  className={clsx(
                    'h-7 rounded-md px-2.5 text-[11px]',
                    index === 0 ? 'bg-primary font-semibold text-primary-foreground' : 'text-muted-foreground hover:bg-secondary',
                  )}
                >
                  {mode}
                </button>
              ))}
            </div>
          </div>

          <div className="min-h-[34rem] flex-1 space-y-3 overflow-auto p-4">
            <article className="ml-auto w-full rounded-xl border border-primary/30 bg-primary/5 p-3 lg:w-[82%]">
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
                    <h3 className="text-xs font-semibold text-foreground">资料处理</h3>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      上传入口、知识库、入库审计、来源画像和质量状态在这里合并展示。
                    </p>
                  </div>
                  <button className="text-[11px] font-semibold text-primary">展开资料详情</button>
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
              </div>
            </article>

            <AssistantAnswer />
          </div>

          <footer className="border-t border-border-subtle bg-secondary/30 p-3">
            <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-end gap-3 rounded-xl border border-border bg-background/40 p-3 max-sm:grid-cols-[auto_1fr]">
              <button className="grid h-10 w-10 place-items-center rounded-lg border border-border bg-card text-foreground" title="添加资料">
                <UploadCloud size={16} />
              </button>
              <textarea
                className="min-h-10 max-h-28 resize-y bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
                placeholder="拖入资料或输入问题。只上传资料时，系统返回摘要、主题、入库状态和可追问方向。"
              />
              <button className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary px-4 text-xs font-semibold text-primary-foreground max-sm:col-span-2 max-sm:justify-center">
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

        <aside className="space-y-3">
          <SideCard title="资料状态" badge={<Chip tone="ok">可引用</Chip>}>
            <SideMetric label="已保存文件" value="3" />
            <SideMetric label="解析成功" value="3" />
            <SideMetric label="观点卡" value="42" />
            <SideMetric label="冲突信息" value="2" />
            <button className="text-left text-[11px] font-semibold text-primary">打开资料确认</button>
          </SideCard>

          <SideCard title="数据状态" badge={<Chip tone="warn">未用于本回答</Chip>}>
            <SideMetric label="数据源" value="9/11 正常" />
            <SideMetric label="基金估值" value="可用" />
            <SideMetric label="质量监控" value="无阻塞" />
            <SideMetric label="成本预算" value="正常" />
            <button className="text-left text-[11px] font-semibold text-primary">查看数据状态</button>
          </SideCard>

          <SideCard title="回答透明度" badge={<Chip tone="ok">可追溯</Chip>}>
            <SideMetric label="资料内事实" value="12" />
            <SideMetric label="系统推断" value="4" />
            <SideMetric label="风险项" value="3" />
            <SideMetric label="待补充" value="3" />
          </SideCard>

          <SideCard title="功能收纳" badge={<Chip>现有能力</Chip>}>
            {sourceCards.map(([title, desc, Icon]) => (
              <div key={title} className="flex items-center gap-2 border-t border-border-subtle pt-2 first:border-t-0 first:pt-0">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-border bg-secondary text-primary">
                  <Icon size={14} />
                </span>
                <span className="min-w-0">
                  <strong className="block truncate text-[11px] text-foreground">{title}</strong>
                  <span className="block truncate text-[10px] text-muted-foreground">{desc}</span>
                </span>
              </div>
            ))}
          </SideCard>

          <SideCard title="高级入口" badge={<Chip tone="warn">默认收起</Chip>}>
            <SideMetric label="验证中心" value={<FlaskConical size={14} />} />
            <SideMetric label="数据修复" value="隐藏" tone="danger" />
            <SideMetric label="系统设置" value={<ShieldCheck size={14} />} />
            <SideMetric label="语义检索" value={<Search size={14} />} />
          </SideCard>
        </aside>
      </main>
    </div>
  )
}
