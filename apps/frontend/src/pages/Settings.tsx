import { useState, useEffect } from 'react'
import {
  KeyRound, CheckCircle2, XCircle,
  Loader2, Save, Eye, EyeOff, RefreshCw, Database, Bot, Clock,
  Share2, AlertTriangle, Lock, Unlock, Wifi, WifiOff, Activity,
  Download, Upload, Play, ExternalLink
} from 'lucide-react'

const API = '/api/v1'

// ── 类型定义 ──────────────────────────────────────────
interface SettingsData {
  openrouter_api_key_set: boolean
  google_ai_api_key_set: boolean
  fred_api_key_set: boolean
  alpha_vantage_api_key_set: boolean
  coingecko_api_key_set: boolean
  briefing_model: string
  fusion_model: string
  extraction_model: string
  speaker_model: string
  strategy_model: string
  committee_model_a: string
  committee_model_b: string
  committee_model_c: string
  committee_model_d: string
  embedding_model: string
  use_mock_event_feed: boolean
  use_mock_market_data: boolean
  use_mock_flow_data: boolean
  app_env: string
  database_url_type: string
  [key: string]: unknown
}

// ── OpenRouter 模型目录（按厂商分组，强→弱排列，标注价格 input/output per 1M tokens）──
interface ModelOption { value: string; label: string; price: string }
interface ModelGroup { vendor: string; models: ModelOption[] }

const OPENROUTER_MODELS: ModelGroup[] = [
  {
    vendor: 'Anthropic',
    models: [
      { value: 'anthropic/claude-opus-4.7',   label: 'Claude Opus 4.7',   price: '$5 / $25' },
      { value: 'anthropic/claude-opus-4.6',   label: 'Claude Opus 4.6',   price: '$5 / $25' },
      { value: 'anthropic/claude-sonnet-4.6',  label: 'Claude Sonnet 4.6', price: '$3 / $15' },
    ],
  },
  {
    vendor: 'Google',
    models: [
      { value: 'google/gemini-3.1-pro-preview',       label: 'Gemini 3.1 Pro',        price: '$2 / $12' },
      { value: 'google/gemini-3-flash-preview',        label: 'Gemini 3 Flash',        price: '$0.5 / $3' },
      { value: 'google/gemini-3.1-flash-lite-preview', label: 'Gemini 3.1 Flash Lite', price: '$0.25 / $1.5' },
    ],
  },
  {
    vendor: 'OpenAI',
    models: [
      { value: 'openai/gpt-5.4',      label: 'GPT-5.4',      price: '$2.5 / $15' },
      { value: 'openai/gpt-5.4-mini',  label: 'GPT-5.4 Mini', price: '$0.75 / $4.5' },
      { value: 'openai/gpt-5.4-nano',  label: 'GPT-5.4 Nano', price: '$0.2 / $1.25' },
    ],
  },
  {
    vendor: '智谱 AI',
    models: [
      { value: 'z-ai/glm-5', label: 'GLM-5', price: '$0.72 / $2.3' },
    ],
  },
  {
    vendor: 'DeepSeek',
    models: [
      { value: 'deepseek/deepseek-v3.2', label: 'DeepSeek V3.2', price: '$0.26 / $0.38' },
    ],
  },
  {
    vendor: 'Qwen',
    models: [
      { value: 'qwen/qwen3.5-9b', label: 'Qwen 3.5 9B',  price: '$0.05 / $0.15' },
      { value: 'qwen/qwen-turbo',  label: 'Qwen Turbo',   price: '$0.03 / $0.10' },
    ],
  },
  {
    vendor: 'Embedding',
    models: [
      { value: 'qwen/qwen3-embedding-4b',       label: 'Qwen3 Embedding 4B',     price: '$0.01/M' },
      { value: 'google/gemini-embedding-001',     label: 'Gemini Embedding',       price: '$0.15/M' },
      { value: 'openai/text-embedding-3-small',   label: 'text-embedding-3-small', price: '$0.02/M' },
    ],
  },
]

// ── 模型分工角色定义 ──
interface RoleConfig {
  key: string
  label: string
  description: string
  settingsKey: string
}

const MODEL_ROLES: RoleConfig[] = [
  { key: 'briefing',    label: '决策简报',        description: '最终成文 / 研究简报',           settingsKey: 'briefing_model' },
  { key: 'fusion',      label: '信号融合',        description: '因果传播链推断 / 多维数据融合', settingsKey: 'fusion_model' },
  { key: 'extraction',  label: '内容抽取',        description: '语义切片 / 高频文本重组',       settingsKey: 'extraction_model' },
  { key: 'strategy',    label: '策略蒸馏',        description: '策略生成与解释',                settingsKey: 'strategy_model' },
  { key: 'speaker',     label: '来源画像',        description: '来源可信度 / 话术特征分析',     settingsKey: 'speaker_model' },
  { key: 'committee_a', label: '委员会 A · 主席',  description: 'Claude Opus — 综合裁决',       settingsKey: 'committee_model_a' },
  { key: 'committee_b', label: '委员会 B · 正方',  description: 'Gemini Pro — 技术基本面',      settingsKey: 'committee_model_b' },
  { key: 'committee_c', label: '委员会 C · 反方',  description: 'GPT-5.4 — 风险质疑',          settingsKey: 'committee_model_c' },
  { key: 'committee_d', label: '委员会 D · 审计',  description: 'GLM-5 — 市场结构分析',        settingsKey: 'committee_model_d' },
  { key: 'embedding',   label: 'Embedding',       description: '语义搜索 / 向量化',            settingsKey: 'embedding_model' },
]

// ── API Key 定义 ──
interface ApiKeyDef {
  id: string
  name: string
  description: string
  settingsField: string
  inputField: string
}

const API_KEYS: ApiKeyDef[] = [
  { id: 'openrouter',    name: 'OpenRouter API Key',    description: 'LLM 统一调用（全部模型经 OpenRouter 路由）', settingsField: 'openrouter_api_key_set', inputField: 'openrouter_api_key' },
  { id: 'google_ai',     name: 'Google AI API Key',     description: '视频转文字（Gemini 2.5 Flash 免费层）',      settingsField: 'google_ai_api_key_set',  inputField: 'google_ai_api_key' },
  { id: 'fred',          name: 'FRED API Key',          description: '美联储经济数据（宏观指标）',                 settingsField: 'fred_api_key_set',       inputField: 'fred_api_key' },
  { id: 'alpha_vantage', name: 'Alpha Vantage API Key', description: '传统金融行情补充数据源',                     settingsField: 'alpha_vantage_api_key_set', inputField: 'alpha_vantage_api_key' },
  { id: 'coingecko',     name: 'CoinGecko API Key',     description: '加密货币行情（可选，有免费层）',             settingsField: 'coingecko_api_key_set',  inputField: 'coingecko_api_key' },
]

// ── 小组件 ──────────────────────────────────────────
function StatusBadge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono ${
      ok ? 'bg-accent/15 text-accent' : 'bg-muted text-muted-foreground'
    }`}>
      {ok ? <CheckCircle2 size={11} className="text-accent" /> : <XCircle size={11} />}
      {label}
    </span>
  )
}

function LockedModelSelect({ value, onChange, role }: {
  value: string; onChange: (v: string) => void; role: RoleConfig
}) {
  const [locked, setLocked] = useState(true)
  const currentModel = OPENROUTER_MODELS.flatMap(g => g.models).find(m => m.value === value)

  return (
    <div className="p-3 rounded-lg bg-muted/20 border border-border hover:border-border/80 transition-colors">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-mono font-semibold text-foreground whitespace-nowrap">{role.label}</span>
          <span className="text-xs 2xl:text-sm text-muted-foreground truncate">{role.description}</span>
        </div>
        <button
          onClick={() => setLocked(l => !l)}
          className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-xs 2xl:text-sm font-mono transition-colors shrink-0 ml-2 ${
            locked
              ? 'bg-amber-500/10 text-amber-500 hover:bg-amber-500/20'
              : 'bg-accent/10 text-accent hover:bg-accent/20'
          }`}
          title={locked ? '点击解锁以切换模型' : '点击锁定模型'}
        >
          {locked ? <Lock size={10} /> : <Unlock size={10} />}
          {locked ? '已锁定' : '已解锁'}
        </button>
      </div>
      {locked ? (
        <div className="flex items-center gap-2 px-2.5 py-1.5 rounded bg-input border border-border text-xs font-mono text-foreground/80">
          <span className="flex-1 truncate">{currentModel?.label || value || '未设置'}</span>
          {currentModel && <span className="text-muted-foreground text-xs 2xl:text-sm shrink-0">{currentModel.price}</span>}
        </div>
      ) : (
        <select
          value={value}
          onChange={e => { onChange(e.target.value); setLocked(true) }}
          className="w-full bg-input border border-accent/40 rounded px-2.5 py-1.5 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
        >
          {OPENROUTER_MODELS.map(group => (
            <optgroup key={group.vendor} label={`── ${group.vendor} ──`}>
              {group.models.map(m => (
                <option key={m.value} value={m.value}>
                  {m.label}  [{m.price}]
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      )}
    </div>
  )
}

// ══════════════════════════════════════════════════════
// 主组件
// ══════════════════════════════════════════════════════
export default function Settings() {
  const [data, setData] = useState<SettingsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ success: boolean; msg: string } | null>(null)
  const [saved, setSaved] = useState(false)

  // ── 模型分工 ──
  const [roleModels, setRoleModels] = useState<Record<string, string>>({})

  // ── API Key 输入 + HFK 模式 ──
  const [keyVisible, setKeyVisible] = useState<Record<string, boolean>>({})

  // ── 数据接口健康 ──
  interface ServiceHealth {
    id: string; name: string; url: string; online: boolean
    latency_ms: number | null; error: string | null
  }
  interface ExternalApi {
    id: string; name: string; requires_key: boolean
    key_set: boolean; note: string
  }
  const [dsServices, setDsServices] = useState<ServiceHealth[]>([])
  const [dsApis, setDsApis] = useState<ExternalApi[]>([])
  const [dsMock, setDsMock] = useState<Record<string, boolean>>({})
  const [dsLoading, setDsLoading] = useState(false)

  // ── 配额状态 ──
  const [quotaStatus, setQuotaStatus] = useState<Array<{
    resource: string; freq: string; allowed: boolean
    cooldown_remaining_sec: number; cooldown_total_sec: number
  }>>([])
  const [quotaLoading, setQuotaLoading] = useState(false)

  // ── 社交媒体 ──
  interface CookieInfo {
    platform: string; has_cookie: boolean; updated_at: string | null
    estimated_ttl_days: number; expires_at: string | null
    remaining_days: number | null; is_expired: boolean; needs_refresh: boolean
  }
  interface SocialAccounts {
    twitter: string[]; weibo: string[]; xiaohongshu: string[]
    douyin: string[]; wechat_mp: string[]
  }
  interface SocialServiceConfig {
    rsshub_base_url: string; mediacrawler_api_url: string
    wemprss_base_url: string; wemprss_api_token_set: boolean
    google_ai_api_key_set: boolean; video_transcribe_model: string
    video_transcribe_fallback: string
  }

  const [cookieStatus, setCookieStatus] = useState<CookieInfo[]>([])
  const [socialServices, setSocialServices] = useState<SocialServiceConfig | null>(null)
  const [socialLoading, setSocialLoading] = useState(false)
  const [showSocialPanel, setShowSocialPanel] = useState(false)

  // MediaCrawler 浏览器登录态（小红书 + 抖音 + 微信）
  interface LoginInfo {
    platform: string
    logged_in: boolean
    cookie_age_days: number | null
    estimated_ttl_days: number
    expires_in_days: number | null
    needs_relogin: boolean
    user_data_dir: string | null
    note: string | null
  }
  const [loginStatus, setLoginStatus] = useState<LoginInfo[]>([])
  const [mediacrawlerExists, setMediacrawlerExists] = useState<boolean>(true)
  const [savingPlatform, setSavingPlatform] = useState<string | null>(null)

  // MediaCrawler 爬虫任务状态
  interface CrawlerState {
    status: 'idle' | 'running' | 'stopping' | 'error' | 'unreachable'
    platform: string | null
    started_at: string | null
  }
  const [crawlerState, setCrawlerState] = useState<CrawlerState>({ status: 'idle', platform: null, started_at: null })
  const [crawlFileStats, setCrawlFileStats] = useState<Record<string, { file_count: number; latest_mtime: string | null }>>({})
  const [triggerMsg, setTriggerMsg] = useState<string | null>(null)

  const [twitterCookie, setTwitterCookie] = useState('')
  const [weiboCookie, setWeiboCookie] = useState('')
  const [xhsCookie, setXhsCookie] = useState('')
  const [douyinCookie, setDouyinCookie] = useState('')
  const [cookieSaving, setCookieSaving] = useState(false)

  const [twitterAccounts, setTwitterAccounts] = useState('')
  const [weiboUids, setWeiboUids] = useState('')
  const [xhsUserIds, setXhsUserIds] = useState('')
  const [douyinUserIds, setDouyinUserIds] = useState('')
  const [wechatAccounts, setWechatAccounts] = useState('')
  const [accountsSaving, setAccountsSaving] = useState(false)

  const [rsshubUrl, setRsshubUrl] = useState('')
  const [mediacrawlerUrl, setMediacrawlerUrl] = useState('')
  const [wemprssUrl, setWemprssUrl] = useState('')
  const [servicesSaving, setServicesSaving] = useState(false)

  // ── 新增：Cookie 测试 / 导入导出 / 最近内容预览 ──
  interface TestResult {
    success: boolean
    reason?: string
    detail?: string
    fetched_count?: number
    sample?: { title?: string; author?: string; published?: string; source_url?: string }
  }
  const [testingPlatform, setTestingPlatform] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({})
  const [importing, setImporting] = useState(false)

  interface RecentItem {
    id: string; source_type: string; title: string; author?: string
    publisher?: string; url?: string; status: string
    created_at?: string; preview: string; theme?: string; word_count?: number
  }
  const [recentItems, setRecentItems] = useState<RecentItem[]>([])
  const [recentPlatform, setRecentPlatform] = useState<string>('all')
  const [recentLoading, setRecentLoading] = useState(false)

  // ── 数据加载 ──
  useEffect(() => {
    fetch(`${API}/settings`).then(r => r.json()).then((d: SettingsData) => {
      setData(d)
      const rm: Record<string, string> = {}
      MODEL_ROLES.forEach(r => { rm[r.settingsKey] = (d[r.settingsKey] as string) || '' })
      setRoleModels(rm)
      setLoading(false)
    }).catch(() => setLoading(false))
    fetchQuota()
    fetchSocialStatus()
    fetchDatasourceHealth()
  }, [])

  // ── 单独保存 Key ──
  const handleSaveKeys = async () => {
    setSaving(true)
    try {
      const fresh: SettingsData = await fetch(`${API}/settings`).then(r => r.json())
      setData(fresh)
      setTestResult({ success: true, msg: 'API Key 由 SOPS 管理；当前页面仅刷新注入状态' })
    } catch {}
    setSaving(false)
  }

  // ── 数据接口健康检查 ──
  const fetchDatasourceHealth = async () => {
    setDsLoading(true)
    try {
      const r = await fetch(`${API}/settings/datasource-health`)
      const d = await r.json()
      setDsServices(d.services || [])
      setDsApis(d.external_apis || [])
      setDsMock(d.mock_status || {})
    } catch {}
    setDsLoading(false)
  }

  // ── 保存全部设置 ──
  const handleSave = async () => {
    setSaving(true); setSaved(false)
    const body: Record<string, unknown> = {
      ...roleModels,
      use_mock_event_feed: false,
      use_mock_market_data: false,
      use_mock_flow_data: false,
    }
    try {
      await fetch(`${API}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const fresh: SettingsData = await fetch(`${API}/settings`).then(r => r.json())
      setData(fresh)
      setSaved(true); setTimeout(() => setSaved(false), 3000)
    } catch {}
    setSaving(false)
  }

  // ── 测试连接 ──
  const handleTest = async () => {
    setTesting(true); setTestResult(null)
    try {
      const d = await fetch(`${API}/settings/test-connection`, { method: 'POST' }).then(r => r.json())
      setTestResult(
        d.success
          ? { success: true, msg: `${d.provider} · ${d.model} · ${d.latency_ms}ms` }
          : { success: false, msg: d.error || '连接失败' }
      )
    } catch { setTestResult({ success: false, msg: '网络错误' }) }
    setTesting(false)
  }

  // ── 配额 ──
  const fetchQuota = async () => {
    setQuotaLoading(true)
    try {
      const d = await fetch(`${API}/settings/quota-status`).then(r => r.json())
      setQuotaStatus(Array.isArray(d) ? d : [])
    } catch {}
    setQuotaLoading(false)
  }

  const resetQuota = async (resource: string, freq: string) => {
    await fetch(`${API}/settings/quota-reset/${resource}?freq=${freq}`, { method: 'POST' })
    fetchQuota()
  }

  // ── 社交媒体 ──
  const fetchSocialStatus = async () => {
    setSocialLoading(true)
    try {
      const [cookies, accounts, services, loginResp] = await Promise.all([
        fetch(`${API}/social-media/cookies`).then(r => r.json()),
        fetch(`${API}/social-media/accounts`).then(r => r.json()),
        fetch(`${API}/social-media/services`).then(r => r.json()),
        fetch(`${API}/social-media/login-status`).then(r => r.json()).catch(() => ({ data: [], mediacrawler_exists: false })),
      ])
      setCookieStatus(cookies.data || [])
      setLoginStatus(loginResp.data || [])
      setMediacrawlerExists(loginResp.mediacrawler_exists !== false)
      const acc = accounts.data as SocialAccounts
      setTwitterAccounts(acc?.twitter?.join(',') || '')
      setWeiboUids(acc?.weibo?.join(',') || '')
      setXhsUserIds(acc?.xiaohongshu?.join(',') || '')
      setDouyinUserIds(acc?.douyin?.join(',') || '')
      setWechatAccounts(acc?.wechat_mp?.join(',') || '')
      const svc = services.data as SocialServiceConfig
      setSocialServices(svc)
      setRsshubUrl(svc?.rsshub_base_url || '')
      setMediacrawlerUrl(svc?.mediacrawler_api_url || '')
      setWemprssUrl(svc?.wemprss_base_url || '')
      fetchCrawlerStatus()
    } catch {}
    setSocialLoading(false)
  }

  const fetchCrawlerStatus = async () => {
    try {
      const r = await fetch(`${API}/social-media/mediacrawler/status`).then(r => r.json())
      if (r.success) {
        setCrawlerState(r.crawler as CrawlerState)
        setCrawlFileStats(r.output_files || {})
      }
    } catch {}
  }

  // 爬虫运行中时每 5 秒轮询一次状态
  useEffect(() => {
    if (crawlerState.status !== 'running') return
    const t = setInterval(() => { fetchCrawlerStatus() }, 5000)
    return () => clearInterval(t)
  }, [crawlerState.status])

  const triggerCrawl = async (platform: 'xhs' | 'douyin') => {
    setTriggerMsg(null)
    try {
      const r = await fetch(`${API}/social-media/mediacrawler/trigger/${platform}`, { method: 'POST' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) {
        setTriggerMsg(`❌ ${j.detail || '触发失败'}`)
        return
      }
      if (j.status === 'already_running') {
        setTriggerMsg(`⚠ ${j.message}`)
      } else {
        setTriggerMsg(`✓ 已触发 ${platform} 抓取 (headless, ${j.creator_count} 博主)`)
        setCrawlerState({ status: 'running', platform, started_at: new Date().toISOString() })
      }
      fetchCrawlerStatus()
    } catch (e: any) {
      setTriggerMsg(`❌ 请求错误: ${e.message}`)
    }
    setTimeout(() => setTriggerMsg(null), 6000)
  }

  const stopCrawl = async () => {
    try {
      await fetch(`${API}/social-media/mediacrawler/stop`, { method: 'POST' })
      fetchCrawlerStatus()
    } catch {}
  }

  const savePlatformCookie = async (platform: 'twitter' | 'weibo' | 'xhs' | 'douyin', value: string) => {
    if (!value.trim()) return
    setSavingPlatform(platform)
    try {
      await fetch(`${API}/social-media/cookies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [`${platform}_cookie`]: value }),
      })
      // 清空对应输入框
      if (platform === 'twitter') setTwitterCookie('')
      if (platform === 'weibo') setWeiboCookie('')
      if (platform === 'xhs') setXhsCookie('')
      if (platform === 'douyin') setDouyinCookie('')
      fetchSocialStatus()
    } catch (e) { /* ignore */ }
    setSavingPlatform(null)
  }

  const handleSaveCookies = async () => {
    setCookieSaving(true)
    try {
      const body: Record<string, string> = {}
      if (twitterCookie) body.twitter_cookie = twitterCookie
      if (weiboCookie) body.weibo_cookie = weiboCookie
      if (xhsCookie) body.xhs_cookie = xhsCookie
      if (douyinCookie) body.douyin_cookie = douyinCookie
      await fetch(`${API}/social-media/cookies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      setTwitterCookie(''); setWeiboCookie(''); setXhsCookie(''); setDouyinCookie('')
      fetchSocialStatus()
    } catch {}
    setCookieSaving(false)
  }

  const handleSaveAccounts = async () => {
    setAccountsSaving(true)
    try {
      await fetch(`${API}/social-media/accounts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          twitter_accounts: twitterAccounts,
          weibo_uids: weiboUids,
          xhs_user_ids: xhsUserIds,
          douyin_user_ids: douyinUserIds,
          wechat_accounts: wechatAccounts,
        }),
      })
      fetchSocialStatus()
    } catch {}
    setAccountsSaving(false)
  }

  const handleSaveServices = async () => {
    setServicesSaving(true)
    try {
      const body: Record<string, string> = {}
      if (rsshubUrl) body.rsshub_base_url = rsshubUrl
      if (mediacrawlerUrl) body.mediacrawler_api_url = mediacrawlerUrl
      if (wemprssUrl) body.wemprss_base_url = wemprssUrl
      await fetch(`${API}/social-media/services`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      fetchSocialStatus()
    } catch {}
    setServicesSaving(false)
  }

  const PLATFORM_LABELS: Record<string, string> = {
    twitter: 'Twitter/X', weibo: '微博', xhs: '小红书',
    douyin: '抖音', wechat: '微信公众号',
  }

  // ── 导出配置为 JSON 文件 ──
  const handleExportConfig = async () => {
    try {
      const r = await fetch(`${API}/social-media/export`)
      const payload = await r.json()
      const blob = new Blob([JSON.stringify(payload, null, 2)],
        { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
      a.href = url
      a.download = `social-config-${ts}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      alert('导出失败: ' + String(e))
    }
  }

  // ── 从 JSON 文件导入配置 ──
  const handleImportConfig = async (file: File) => {
    setImporting(true)
    try {
      const text = await file.text()
      const parsed = JSON.parse(text)
      const data = parsed.data || parsed   // 兼容直接粘贴 data 对象
      const r = await fetch(`${API}/social-media/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data, overwrite: true }),
      })
      const result = await r.json()
      alert(`导入成功：已更新 ${result.count} 项配置\n${(result.imported_keys || []).join(', ')}`)
      fetchSocialStatus()
    } catch (e) {
      alert('导入失败: ' + String(e))
    }
    setImporting(false)
  }

  // ── 一键测试指定平台 Cookie 有效性 ──
  const handleTestCookie = async (platform: string) => {
    setTestingPlatform(platform)
    try {
      const r = await fetch(`${API}/social-media/cookies/test/${platform}`,
        { method: 'POST' })
      const result: TestResult = await r.json()
      setTestResults(prev => ({ ...prev, [platform]: result }))
    } catch (e) {
      setTestResults(prev => ({ ...prev,
        [platform]: { success: false, reason: 'network', detail: String(e) } }))
    }
    setTestingPlatform(null)
  }

  // ── 加载最近入库的社媒内容 ──
  const fetchRecentItems = async (platform: string = recentPlatform) => {
    setRecentLoading(true)
    try {
      const url = platform === 'all'
        ? `${API}/social-media/recent?limit=20`
        : `${API}/social-media/recent?platform=${platform}&limit=20`
      const r = await fetch(url)
      const j = await r.json()
      setRecentItems(j.data || [])
      setRecentPlatform(platform)
    } catch {
      setRecentItems([])
    }
    setRecentLoading(false)
  }

  const fmtRemaining = (sec: number) => {
    if (sec <= 0) return '可调用'
    if (sec < 60) return `${sec}s`
    if (sec < 3600) return `${Math.ceil(sec / 60)}min`
    return `${Math.ceil(sec / 3600)}h`
  }

  // ── Loading ──
  if (loading) return (
    <div className="flex-1 flex items-center justify-center h-full text-muted-foreground">
      <Loader2 size={20} className="animate-spin mr-2" /> 加载中...
    </div>
  )

  // ══════════════════════════════════════════════════════
  // 渲染
  // ══════════════════════════════════════════════════════
  return (
    <div className="flex-1 overflow-auto">
      <div className="max-w-[1400px] 2xl:max-w-[1600px] mx-auto p-6 2xl:p-8 pb-28">
      {/* 页面头 */}
      <div className="page-header !mb-5 !pb-4">
        <span className="section-label">Settings</span>
        <h1 className="text-2xl 2xl:text-3xl font-semibold text-foreground">系统设置</h1>
        <p className="text-sm 2xl:text-base text-muted-foreground mt-1">
          管理 API Key、模型分工和数据源配置 · 全部 LLM 调用统一经 OpenRouter 路由 · 温度由系统自动调节
        </p>
      </div>

      {/* ════════════════════════════════════════════════════
          ZONE 1 · 配置项 CONFIGURATION
          需要用户输入/修改的设置项
          ════════════════════════════════════════════════════ */}
      <div className="zone-header">
        <span className="zone-eyebrow">01 · Configuration</span>
        <h2 className="zone-title">配置项</h2>
        <span className="zone-desc hidden md:inline">管理 Key、模型分工等需要手动修改的参数</span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 mb-8">

      {/* ════════ 1. API Key 管理 ════════ */}
      <section className="p-5 2xl:p-6 rounded-lg bg-card border border-border h-fit">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm 2xl:text-base font-mono font-semibold text-foreground uppercase tracking-wider flex items-center gap-2">
            <KeyRound size={16} className="text-primary"/> API Key 管理
          </h2>
          <span className="flex items-center gap-1 px-2 py-0.5 rounded text-xs 2xl:text-sm font-mono bg-primary/15 text-primary">
            <Lock size={10}/> SOPS
          </span>
        </div>
        <p className="text-xs 2xl:text-sm text-muted-foreground mb-4">
          API Key 由 SOPS / secrets env 注入，当前页面只展示运行态配置状态；如需新增或轮换密钥，请更新 SOPS 后重启后端。
        </p>
        <p className="hidden">
          API Key 状态由后端运行环境返回，本页不会接收或保存明文密钥。
        </p>

        <div className="space-y-3">
          {API_KEYS.map(k => {
            const isSet = data?.[k.settingsField] as boolean ?? false
            const visible = keyVisible[k.id] || false

            return (
              <div key={k.id} className="p-3 rounded-lg bg-muted/20 border border-border">
                <div className="flex items-center justify-between mb-1.5">
                  <div>
                    <span className="text-xs font-mono font-semibold text-foreground">{k.name}</span>
                    <p className="text-xs 2xl:text-sm text-muted-foreground mt-0.5">{k.description}</p>
                  </div>
                  <StatusBadge ok={isSet} label={isSet ? '已配置' : '未设置'} />
                </div>
                <div className="relative">
                  <input
                    type="password"
                    value={isSet ? 'SOPS injected' : 'Not injected'}
                    readOnly
                    disabled
                    placeholder={isSet ? '••••••••••••• （留空保持原值）' : `填入 ${k.name}...`}
                    className="w-full bg-muted/30 border border-border rounded px-3 py-2 text-xs font-mono text-muted-foreground placeholder:text-muted-foreground focus:outline-none pr-9 cursor-not-allowed"
                  />
                  <button
                    onClick={() => setKeyVisible(prev => ({ ...prev, [k.id]: !visible }))}
                    disabled
                    className="absolute right-2 top-2 text-muted-foreground/60 cursor-not-allowed"
                  >
                    {visible ? <EyeOff size={14}/> : <Eye size={14}/>}
                  </button>
                </div>
              </div>
            )
          })}
        </div>

        {/* 刷新 Key 状态按钮 */}
        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={handleSaveKeys}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded text-xs font-mono font-semibold hover:opacity-90 disabled:opacity-40 transition-opacity"
          >
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            刷新密钥状态
          </button>
          {false && (
            <span className="flex items-center gap-1 text-xs font-mono text-accent">
              <CheckCircle2 size={12} /> 密钥状态已刷新
            </span>
          )}
        </div>
      </section>

      {/* ════════ 3. 模型分工配置（移入 Zone 1）════════ */}
      <section className="p-5 2xl:p-6 rounded-lg bg-card border border-border h-fit">
        <h2 className="text-sm 2xl:text-base font-mono font-semibold text-foreground uppercase tracking-wider mb-1 flex items-center gap-2">
          <Bot size={16} className="text-primary"/> 模型分工配置
        </h2>
        <p className="text-xs text-muted-foreground mb-4">
          各 Skill 使用不同模型，温度和 Token 上限由系统根据模型和角色自动调节。模型默认锁定，需先解锁才能切换。价格为 Input/Output per 1M tokens。
        </p>

        {/* 核心 Skills */}
        <div className="space-y-2 mb-4">
          <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider">核心 Skill 模型</p>
          {MODEL_ROLES.filter(r => !r.key.startsWith('committee_') && r.key !== 'embedding').map(role => (
            <LockedModelSelect
              key={role.key}
              value={roleModels[role.settingsKey] || ''}
              onChange={v => setRoleModels(prev => ({ ...prev, [role.settingsKey]: v }))}
              role={role}
            />
          ))}
        </div>

        {/* 委员会席位 */}
        <div className="space-y-2 mb-4">
          <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider">四模型委员会</p>
          {MODEL_ROLES.filter(r => r.key.startsWith('committee_')).map(role => (
            <LockedModelSelect
              key={role.key}
              value={roleModels[role.settingsKey] || ''}
              onChange={v => setRoleModels(prev => ({ ...prev, [role.settingsKey]: v }))}
              role={role}
            />
          ))}
        </div>

        {/* Embedding */}
        <div className="space-y-2">
          <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider">向量化</p>
          {MODEL_ROLES.filter(r => r.key === 'embedding').map(role => (
            <LockedModelSelect
              key={role.key}
              value={roleModels[role.settingsKey] || ''}
              onChange={v => setRoleModels(prev => ({ ...prev, [role.settingsKey]: v }))}
              role={role}
            />
          ))}
        </div>
      </section>

      </div>
      {/* ════════ END Zone 1 ════════ */}

      {/* ════════════════════════════════════════════════════
          ZONE 2 · 系统监测 MONITORING
          只读状态面板：系统/配额/数据源接口
          ════════════════════════════════════════════════════ */}
      <div className="zone-header">
        <span className="zone-eyebrow">02 · Monitoring</span>
        <h2 className="zone-title">系统监测</h2>
        <span className="zone-desc hidden md:inline">只读状态 · 无需修改，仅供查看和刷新</span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-5 mb-8">

      {/* ════════ 2. 系统状态 ════════ */}
      <section className="p-5 2xl:p-6 rounded-lg bg-card border border-border">
        <h2 className="text-sm 2xl:text-base font-mono font-semibold text-foreground uppercase tracking-wider mb-3 flex items-center gap-2">
          <Database size={16} className="text-primary"/> 系统状态
        </h2>
        <div className="flex flex-wrap gap-2">
          <StatusBadge ok={data?.openrouter_api_key_set ?? false} label="OpenRouter" />
          <StatusBadge ok={data?.google_ai_api_key_set ?? false} label="Google AI" />
          <StatusBadge ok={data?.fred_api_key_set ?? false} label="FRED" />
          <StatusBadge ok={data?.coingecko_api_key_set ?? false} label="CoinGecko" />
          <StatusBadge ok={data?.database_url_type === 'postgresql'} label="PostgreSQL" />
          <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono ${
            data?.app_env === 'production' ? 'bg-destructive/15 text-destructive' : 'bg-secondary text-secondary-foreground'
          }`}>
            {data?.app_env === 'production' ? '生产环境' : '开发环境'}
          </span>
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-mono bg-primary/10 text-primary">
            OpenRouter 统一路由
          </span>
        </div>
      </section>

      {/* ════════ 6. 配额状态（移到 Row A 右栏）════════ */}
      <section className="p-5 2xl:p-6 rounded-lg bg-card border border-border">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm 2xl:text-base font-mono font-semibold text-foreground uppercase tracking-wider flex items-center gap-2">
            <Clock size={16} className="text-primary"/> API 调用配额状态
          </h2>
          <button onClick={fetchQuota} disabled={quotaLoading}
            className="flex items-center gap-1 text-xs 2xl:text-sm font-mono text-primary hover:underline disabled:opacity-50">
            {quotaLoading ? <Loader2 size={10} className="animate-spin"/> : <RefreshCw size={10}/>}
            刷新
          </button>
        </div>
        <p className="text-xs 2xl:text-sm text-muted-foreground mb-3">
          「可调用」= 下次提问时拉取最新数据；冷却中 = 使用 DB 存量，不消耗配额。
        </p>
        <div className="grid grid-cols-2 gap-2">
          {quotaStatus.map(q => {
            const pct = q.cooldown_remaining_sec > 0
              ? Math.round((1 - q.cooldown_remaining_sec / q.cooldown_total_sec) * 100)
              : 100
            return (
              <div key={`${q.resource}-${q.freq}`}
                className="p-2 rounded bg-muted/40 border border-border flex flex-col gap-1">
                <div className="flex items-center justify-between">
                  <span className="text-xs 2xl:text-sm font-mono text-foreground truncate" title={q.resource}>
                    {q.resource.replace(/_/g, ' ')}
                  </span>
                  <span className={`text-xs 2xl:text-sm font-mono ml-1 shrink-0 ${
                    q.allowed ? 'text-emerald-400' : 'text-amber-400'
                  }`}>
                    {fmtRemaining(q.cooldown_remaining_sec)}
                  </span>
                </div>
                <div className="w-full h-1 bg-border rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      q.allowed ? 'bg-emerald-400' : 'bg-amber-400'
                    }`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] 2xl:text-xs font-mono text-muted-foreground">{q.freq}</span>
                  {!q.allowed && (
                    <button
                      onClick={() => resetQuota(q.resource, q.freq)}
                      className="text-[10px] 2xl:text-xs font-mono text-primary hover:underline">
                      强制刷新
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </section>

      {/* ════════ 4. 数据接口状态 ════════ */}
      <section className="p-5 2xl:p-6 rounded-lg bg-card border border-border h-fit">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm 2xl:text-base font-mono font-semibold text-foreground uppercase tracking-wider flex items-center gap-2">
            <Activity size={16} className="text-primary"/> 数据接口状态
          </h2>
          <button onClick={fetchDatasourceHealth} disabled={dsLoading}
            className="flex items-center gap-1 text-xs 2xl:text-sm font-mono text-primary hover:underline disabled:opacity-50">
            {dsLoading ? <Loader2 size={10} className="animate-spin"/> : <RefreshCw size={10}/>}
            刷新
          </button>
        </div>

        {/* Mock 警告 */}
        {Object.values(dsMock).some(v => v) && (
          <div className="mb-3 p-2 rounded bg-amber-400/10 border border-amber-400/30 flex items-center gap-2">
            <AlertTriangle size={12} className="text-amber-400 shrink-0"/>
            <p className="text-xs 2xl:text-sm font-mono text-amber-400">
              检测到 Mock 数据开关仍处于开启状态，点击下方「保存设置」将自动关闭全部 Mock。
            </p>
          </div>
        )}

        {/* 自建服务 */}
        <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider mb-2">自建服务（需 Docker）</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-4">
          {dsServices.map(s => (
            <div key={s.id} className={`p-2.5 rounded border flex flex-col gap-1 ${
              s.online ? 'border-accent/30 bg-accent/5' : 'border-destructive/40 bg-destructive/5'
            }`}>
              <div className="flex items-center justify-between">
                <span className="text-xs 2xl:text-sm font-mono font-medium text-foreground">{s.name}</span>
                {s.online ? <Wifi size={12} className="text-accent"/> : <WifiOff size={12} className="text-destructive"/>}
              </div>
              <div className="flex items-center justify-between">
                <span className={`text-xs 2xl:text-sm font-mono ${s.online ? 'text-accent' : 'text-destructive'}`}>
                  {s.online ? `在线 · ${s.latency_ms}ms` : '离线'}
                </span>
                <span className="text-[10px] 2xl:text-xs font-mono text-muted-foreground truncate ml-1" title={s.url}>{s.url}</span>
              </div>
              {!s.online && s.error && (
                <p className="text-[10px] 2xl:text-xs font-mono text-destructive/70 truncate" title={s.error}>{s.error}</p>
              )}
            </div>
          ))}
          {dsServices.length === 0 && !dsLoading && (
            <p className="text-xs 2xl:text-sm text-muted-foreground col-span-full">加载中...</p>
          )}
        </div>

        {/* 外部 API */}
        <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider mb-2">外部数据 API</p>
        <div className="grid grid-cols-2 gap-2">
          {dsApis.map(a => (
            <div key={a.id} className="p-2 rounded border border-border bg-muted/20 flex items-center justify-between">
              <div className="min-w-0">
                <span className="text-xs 2xl:text-sm font-mono font-medium text-foreground">{a.name}</span>
                <p className="text-[10px] 2xl:text-xs font-mono text-muted-foreground">{a.note}</p>
              </div>
              <div className="shrink-0 ml-2">
                {a.key_set
                  ? <span className="flex items-center gap-1 text-xs 2xl:text-sm font-mono text-accent"><CheckCircle2 size={10}/> 可用</span>
                  : <span className="flex items-center gap-1 text-xs 2xl:text-sm font-mono text-destructive"><XCircle size={10}/> 缺 Key</span>
                }
              </div>
            </div>
          ))}
        </div>
      </section>

      </div>
      {/* ════════ END Zone 2 ════════ */}

      {/* ════════════════════════════════════════════════════
          ZONE 3 · 社交媒体接入 SOCIAL MEDIA
          包含配置（Cookie、博主、服务 URL）与状态（有效期、入口、最近内容）
          ════════════════════════════════════════════════════ */}
      <div className="zone-header">
        <span className="zone-eyebrow">03 · Social Media</span>
        <h2 className="zone-title">社交媒体接入</h2>
        <span className="zone-desc hidden md:inline">Twitter/X · 微博 · 小红书 · 抖音 · 微信公众号</span>
      </div>

      {/* ════════ 5. 社交媒体信息源（全宽）════════ */}
      <section className="mb-5 p-5 2xl:p-6 rounded-lg bg-card border border-border">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm 2xl:text-base font-mono font-semibold text-foreground uppercase tracking-wider flex items-center gap-2">
            <Share2 size={16} className="text-primary"/> 社交媒体信息源
          </h2>
          <div className="flex items-center gap-2">
            <button onClick={fetchSocialStatus} disabled={socialLoading}
              className="flex items-center gap-1 text-xs 2xl:text-sm font-mono text-primary hover:underline disabled:opacity-50">
              {socialLoading ? <Loader2 size={10} className="animate-spin"/> : <RefreshCw size={10}/>}
              刷新
            </button>
            <button onClick={() => setShowSocialPanel(v => !v)}
              className="text-xs text-muted-foreground hover:text-foreground font-mono">
              {showSocialPanel ? '收起' : '展开'}
            </button>
          </div>
        </div>

        {/* Cookie 状态总览 */}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-3">
          {cookieStatus.map(c => (
            <div key={c.platform}
              className={`p-2 rounded border flex flex-col gap-1 ${
                c.is_expired ? 'border-destructive/40 bg-destructive/5' :
                c.needs_refresh ? 'border-amber-400/40 bg-amber-400/5' :
                c.has_cookie ? 'border-accent/30 bg-accent/5' :
                'border-border bg-muted/30'
              }`}>
              <div className="flex items-center justify-between">
                <span className="text-xs 2xl:text-sm font-mono font-medium text-foreground">
                  {PLATFORM_LABELS[c.platform] || c.platform}
                </span>
                {c.is_expired && <AlertTriangle size={11} className="text-destructive"/>}
                {!c.is_expired && c.needs_refresh && <AlertTriangle size={11} className="text-amber-400"/>}
                {!c.is_expired && !c.needs_refresh && c.has_cookie && <CheckCircle2 size={11} className="text-accent"/>}
              </div>
              <span className={`text-xs 2xl:text-sm font-mono ${
                c.is_expired ? 'text-destructive' :
                c.needs_refresh ? 'text-amber-400' :
                c.has_cookie ? 'text-accent' :
                'text-muted-foreground'
              }`}>
                {!c.has_cookie ? '未配置' :
                 c.is_expired ? '已过期' :
                 c.needs_refresh ? `即将过期（${c.remaining_days}天）` :
                 `剩余 ${c.remaining_days} 天`}
              </span>
            </div>
          ))}
          {cookieStatus.length === 0 && (
            <p className="text-xs 2xl:text-sm text-muted-foreground col-span-full">加载中或后端未连接...</p>
          )}
        </div>

        {/* ═══ 中间件服务控制台（始终可见）═══ */}
        <div className="p-3 rounded-lg bg-muted/30 border border-border space-y-2 mt-3">
          <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider">
            中间件快捷入口
          </p>
          <p className="text-xs 2xl:text-sm text-muted-foreground">
            点击按钮新标签页打开对应服务的管理/文档页面。
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {[
              { name: 'we-mp-rss', desc: '微信公众号 RSS 管理', url: 'http://localhost:8090',
                hint: '在此界面登录微信、添加订阅公众号', platforms: ['微信公众号'] },
              { name: 'RSSHub', desc: 'Twitter/X + 微博 RSS 生成器', url: 'http://localhost:1200',
                hint: '公共只读服务，无需登录；Cookie 在下面的"Cookie 更新"区填写', platforms: ['Twitter/X','微博'] },
              { name: 'MediaCrawler API', desc: '小红书 + 抖音爬虫 API', url: 'http://localhost:8080/docs',
                hint: '首次登录需本地 CLI 扫码，Cookie 已保存后此 API 才能拉取', platforms: ['小红书','抖音'] },
              { name: 'Backend API 文档', desc: 'FastAPI Swagger UI', url: 'http://localhost:8000/docs',
                hint: '查看/调试所有后端接口', platforms: ['所有'] },
            ].map(svc => (
              <a key={svc.name} href={svc.url} target="_blank" rel="noopener noreferrer"
                className="group p-2 rounded border border-border bg-background hover:border-primary/50 hover:bg-muted/50 transition-colors flex flex-col gap-1">
                <div className="flex items-center justify-between">
                  <span className="text-xs 2xl:text-sm font-mono font-semibold text-foreground">
                    {svc.name}
                  </span>
                  <ExternalLink size={11} className="text-muted-foreground group-hover:text-primary"/>
                </div>
                <span className="text-xs 2xl:text-sm text-muted-foreground">{svc.desc}</span>
                <div className="flex flex-wrap gap-1 mt-0.5">
                  {svc.platforms.map(p => (
                    <span key={p} className="text-[10px] 2xl:text-xs font-mono px-1 rounded bg-primary/10 text-primary">
                      {p}
                    </span>
                  ))}
                </div>
                <span className="text-[10px] 2xl:text-xs text-muted-foreground italic mt-0.5">
                  {svc.hint}
                </span>
              </a>
            ))}
          </div>
          <details className="mt-1">
            <summary className="text-xs 2xl:text-sm font-mono text-muted-foreground cursor-pointer hover:text-foreground">
              ▸ 小红书 / 抖音 Cookie 失效了怎么办？
            </summary>
            <div className="mt-1 p-2 rounded bg-background/50 border border-border space-y-1 text-xs 2xl:text-sm font-mono text-muted-foreground">
              <p>在项目目录下另开 PowerShell 终端，运行：</p>
              <pre className="bg-muted/50 p-1.5 rounded text-[10px] 2xl:text-xs whitespace-pre-wrap break-all select-all">
{`cd C:\\example\\MediaCrawler
$env:PLAYWRIGHT_BROWSERS_PATH = "E:\\ms-playwright"
# 小红书重新登录：
uv run main.py --platform xhs --lt qrcode --type search --keywords "测试"
# 抖音重新登录：
uv run main.py --platform dy --lt qrcode --type search --keywords "测试"`}
              </pre>
              <p>浏览器会弹出二维码，用 APP 扫码后 Cookie 自动保存。</p>
            </div>
          </details>
        </div>

        {showSocialPanel && (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 mt-4">
            {/* Cookie 更新 */}
            <div className="p-3 rounded-lg bg-muted/30 border border-border space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider">Cookie 更新</p>
                {loginStatus.length > 0 && (
                  <span className="text-[10px] 2xl:text-xs font-mono text-muted-foreground">
                    MediaCrawler 登录态 · 实时检测
                  </span>
                )}
              </div>
              <p className="text-xs 2xl:text-sm text-muted-foreground">
                每个平台独立保存，粘贴后点击右侧「保存」即刻生效（无需滚到顶部）。
                {!mediacrawlerExists && (
                  <span className="block mt-1 text-amber-400">⚠ 未检测到 MediaCrawler 目录，小红书/抖音登录态不可用</span>
                )}
              </p>

              {/* ── MediaCrawler 爬虫状态横幅 ── */}
              {(crawlerState.status !== 'idle' || triggerMsg) && (
                <div className={`p-2 rounded border flex items-center gap-2 text-xs font-mono ${
                  crawlerState.status === 'running' ? 'bg-primary/10 border-primary/40 text-primary' :
                  crawlerState.status === 'error' ? 'bg-destructive/10 border-destructive/40 text-destructive' :
                  'bg-accent/5 border-accent/30 text-accent'
                }`}>
                  {crawlerState.status === 'running' && <Loader2 size={11} className="animate-spin shrink-0"/>}
                  <div className="flex-1 min-w-0">
                    {crawlerState.status === 'running' && (
                      <>爬虫运行中 · 平台={crawlerState.platform} · 开始于 {crawlerState.started_at?.slice(11, 19)}</>
                    )}
                    {crawlerState.status === 'unreachable' && <>⚠ MediaCrawler 未运行（端口 8080 无响应）</>}
                    {triggerMsg && <div className="text-muted-foreground mt-0.5">{triggerMsg}</div>}
                  </div>
                  {crawlerState.status === 'running' && (
                    <button onClick={stopCrawl} className="shrink-0 px-2 py-0.5 rounded bg-destructive/20 text-destructive hover:bg-destructive hover:text-destructive-foreground text-[10px]">
                      停止
                    </button>
                  )}
                </div>
              )}

              {/* 输出文件汇总 */}
              {Object.keys(crawlFileStats).length > 0 && (
                <div className="flex items-center gap-3 text-[10px] 2xl:text-xs font-mono text-muted-foreground">
                  {(['xhs', 'douyin'] as const).map(p => {
                    const s = crawlFileStats[p]
                    if (!s) return null
                    const fresh = s.latest_mtime ? new Date(s.latest_mtime) : null
                    const hours = fresh ? Math.floor((Date.now() - fresh.getTime()) / 3600000) : null
                    return (
                      <span key={p} className="flex items-center gap-1">
                        <Database size={9}/> {p === 'xhs' ? '小红书' : '抖音'}: {s.file_count} 文件
                        {hours !== null && <span className="opacity-60">· {hours}h 前更新</span>}
                      </span>
                    )
                  })}
                </div>
              )}
              <div className="space-y-2">
                {([
                  { platform: 'twitter', label: 'Twitter/X', val: twitterCookie, set: setTwitterCookie, ph: '粘贴 Twitter cookie...', hasLogin: false },
                  { platform: 'weibo', label: '微博', val: weiboCookie, set: setWeiboCookie, ph: '粘贴微博 cookie...', hasLogin: false },
                  { platform: 'xhs', label: '小红书', val: xhsCookie, set: setXhsCookie, ph: 'MediaCrawler 自动管理，也可手动粘贴', hasLogin: true },
                  { platform: 'douyin', label: '抖音', val: douyinCookie, set: setDouyinCookie, ph: 'MediaCrawler 自动管理，也可手动粘贴', hasLogin: true },
                ] as const).map(f => {
                  const login = loginStatus.find(l => l.platform === f.platform)
                  return (
                    <div key={f.platform}>
                      <div className="flex items-center justify-between gap-2 mb-0.5">
                        <label className="text-xs 2xl:text-sm font-mono text-muted-foreground">{f.label}</label>
                        <div className="flex items-center gap-2">
                          {f.hasLogin && login && (
                            <span className={`text-[10px] 2xl:text-xs font-mono flex items-center gap-1 ${
                              login.logged_in
                                ? login.needs_relogin ? 'text-amber-400' : 'text-accent'
                                : 'text-destructive'
                            }`}>
                              {login.logged_in
                                ? <><CheckCircle2 size={10}/> 浏览器已登录 · {login.cookie_age_days} 天前</>
                                : <><XCircle size={10}/> 浏览器未登录</>
                              }
                            </span>
                          )}
                          {f.hasLogin && (
                            <button
                              onClick={() => triggerCrawl(f.platform as 'xhs' | 'douyin')}
                              disabled={crawlerState.status === 'running'}
                              title={`触发 MediaCrawler 以 headless 模式抓取配置的 ${f.label} 博主`}
                              className="text-[10px] 2xl:text-xs font-mono px-1.5 py-0.5 rounded bg-primary/15 text-primary hover:bg-primary hover:text-primary-foreground disabled:opacity-30 disabled:hover:bg-primary/15 disabled:hover:text-primary transition-colors flex items-center gap-1">
                              <RefreshCw size={9}/> 立即抓取
                            </button>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <input type="text" value={f.val} onChange={e => f.set(e.target.value)}
                          placeholder={f.ph}
                          className="flex-1 bg-input border border-border rounded px-2 py-1.5 text-xs font-mono text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring" />
                        <button
                          onClick={() => savePlatformCookie(f.platform as 'twitter' | 'weibo' | 'xhs' | 'douyin', f.val)}
                          disabled={!f.val.trim() || savingPlatform === f.platform}
                          title="立即保存此平台 Cookie"
                          className="shrink-0 px-2 py-1.5 rounded text-xs font-mono bg-primary/20 text-primary hover:bg-primary hover:text-primary-foreground disabled:opacity-30 disabled:hover:bg-primary/20 disabled:hover:text-primary transition-colors flex items-center gap-1">
                          {savingPlatform === f.platform ? <Loader2 size={11} className="animate-spin"/> : <Save size={11}/>}
                          保存
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
              <button onClick={handleSaveCookies} disabled={cookieSaving || (!twitterCookie && !weiboCookie && !xhsCookie && !douyinCookie)}
                className="mt-2 px-3 py-1.5 rounded text-xs font-mono bg-secondary text-muted-foreground hover:bg-border disabled:opacity-40 flex items-center gap-1.5">
                {cookieSaving ? <Loader2 size={11} className="animate-spin"/> : <Save size={11}/>}
                一次性全部保存
              </button>
            </div>

            {/* 追踪博主 */}
            <div className="p-3 rounded-lg bg-muted/30 border border-border space-y-2">
              <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider">追踪博主/公众号</p>
              <p className="text-xs 2xl:text-sm text-muted-foreground">多个账号用英文逗号分隔。</p>
              <div className="space-y-2">
                {[
                  { label: 'Twitter 用户名', val: twitterAccounts, set: setTwitterAccounts, ph: 'elonmusk,PeterLBrandt' },
                  { label: '微博 UID', val: weiboUids, set: setWeiboUids, ph: '1234567890' },
                  { label: '小红书用户 ID', val: xhsUserIds, set: setXhsUserIds, ph: 'user_id_1,user_id_2' },
                  { label: '抖音 sec_uid', val: douyinUserIds, set: setDouyinUserIds, ph: 'sec_uid_1' },
                  { label: '微信公众号', val: wechatAccounts, set: setWechatAccounts, ph: '某某财经,某某研究院' },
                ].map(f => (
                  <div key={f.label}>
                    <label className="text-xs 2xl:text-sm font-mono text-muted-foreground">{f.label}</label>
                    <input type="text" value={f.val} onChange={e => f.set(e.target.value)}
                      placeholder={f.ph}
                      className="w-full bg-input border border-border rounded px-2 py-1.5 text-xs font-mono text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring mt-0.5" />
                  </div>
                ))}
              </div>
              <button onClick={handleSaveAccounts} disabled={accountsSaving}
                className="mt-2 px-3 py-1.5 rounded text-xs font-mono bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 flex items-center gap-1.5">
                {accountsSaving ? <Loader2 size={11} className="animate-spin"/> : <Save size={11}/>}
                保存博主列表
              </button>
            </div>

            {/* 服务配置 */}
            <div className="p-3 rounded-lg bg-muted/30 border border-border space-y-2">
              <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider">外部服务配置</p>
              <div className="space-y-2">
                {[
                  { label: 'RSSHub URL（Twitter/X + 微博）', val: rsshubUrl, set: setRsshubUrl, ph: 'http://localhost:1200' },
                  { label: 'MediaCrawler API URL（小红书 + 抖音）', val: mediacrawlerUrl, set: setMediacrawlerUrl, ph: 'http://localhost:8080' },
                  { label: 'We-MP-RSS URL（微信公众号）', val: wemprssUrl, set: setWemprssUrl, ph: 'http://localhost:8090' },
                ].map(f => (
                  <div key={f.label}>
                    <label className="text-xs 2xl:text-sm font-mono text-muted-foreground">{f.label}</label>
                    <input type="text" value={f.val} onChange={e => f.set(e.target.value)}
                      placeholder={f.ph}
                      className="w-full bg-input border border-border rounded px-2 py-1.5 text-xs font-mono text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring mt-0.5" />
                  </div>
                ))}
                <div>
                  <div className="flex items-center justify-between">
                    <label className="text-xs 2xl:text-sm font-mono text-muted-foreground">Google AI API Key（视频转文字，免费）</label>
                    <StatusBadge ok={socialServices?.google_ai_api_key_set ?? false} label={socialServices?.google_ai_api_key_set ? '已配置' : '未设置'} />
                  </div>
                  <input type="password" value={socialServices?.google_ai_api_key_set ? 'SOPS injected' : 'Not injected'} readOnly disabled
                    placeholder={socialServices?.google_ai_api_key_set ? '••••••••••••• （留空保持原值）' : '在 aistudio.google.com 免费申请'}
                    className="w-full bg-muted/30 border border-border rounded px-2 py-1.5 text-xs font-mono text-muted-foreground placeholder:text-muted-foreground focus:outline-none mt-0.5 cursor-not-allowed" />
                  <p className="text-[10px] 2xl:text-xs text-muted-foreground mt-0.5">Gemini 2.5 Flash 免费层：每日 1500 次</p>
                </div>
              </div>
              <button onClick={handleSaveServices} disabled={servicesSaving || (!rsshubUrl && !mediacrawlerUrl && !wemprssUrl)}
                className="mt-2 px-3 py-1.5 rounded text-xs font-mono bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 flex items-center gap-1.5">
                {servicesSaving ? <Loader2 size={11} className="animate-spin"/> : <Save size={11}/>}
                保存服务配置
              </button>
            </div>

            {/* ═══ 新增：Cookie 一键测试（全宽）═══ */}
            <div className="xl:col-span-3 p-3 rounded-lg bg-muted/30 border border-border space-y-2">
              <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider">
                一键测试 Cookie 有效性
              </p>
              <p className="text-xs 2xl:text-sm text-muted-foreground">
                点击平台按钮，系统会尝试拉取 1 条内容，验证 Cookie/订阅是否有效。
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                {(['twitter','weibo','xiaohongshu','douyin','wechat_mp'] as const).map(p => {
                  const label = p === 'twitter' ? 'Twitter/X' : p === 'weibo' ? '微博'
                    : p === 'xiaohongshu' ? '小红书' : p === 'douyin' ? '抖音' : '微信公众号'
                  const isLoading = testingPlatform === p
                  const result = testResults[p]
                  return (
                    <button key={p}
                      onClick={() => handleTestCookie(p)}
                      disabled={isLoading}
                      className={`px-2 py-1.5 rounded text-xs 2xl:text-sm font-mono border flex items-center justify-center gap-1 transition-colors ${
                        result?.success ? 'border-accent/50 bg-accent/10 text-accent'
                        : result && !result.success ? 'border-destructive/50 bg-destructive/10 text-destructive'
                        : 'border-border bg-background hover:bg-muted/50 text-foreground'
                      } disabled:opacity-50`}>
                      {isLoading ? <Loader2 size={11} className="animate-spin"/> :
                        result?.success ? <CheckCircle2 size={11}/> :
                        result && !result.success ? <XCircle size={11}/> :
                        <Play size={11}/>}
                      {label}
                    </button>
                  )
                })}
              </div>
              {/* 测试结果详情 */}
              {Object.entries(testResults).map(([platform, r]) => (
                <div key={platform}
                  className={`mt-1 p-2 rounded text-xs 2xl:text-sm font-mono border ${
                    r.success ? 'border-accent/30 bg-accent/5 text-foreground'
                    : 'border-destructive/30 bg-destructive/5 text-destructive'
                  }`}>
                  <div className="flex items-center gap-1">
                    {r.success ? <CheckCircle2 size={10}/> : <XCircle size={10}/>}
                    <span className="font-semibold">{platform}：</span>
                    {r.success
                      ? <>成功拉取 {r.fetched_count} 条</>
                      : <>失败（{r.reason}）{r.detail ? `：${String(r.detail).slice(0, 200)}` : ''}</>}
                  </div>
                  {r.success && r.sample && (
                    <div className="mt-1 ml-4 text-muted-foreground">
                      <div>标题: {r.sample.title || '(无)'}</div>
                      <div>作者: {r.sample.author || '(无)'} · 时间: {(r.sample.published || '').slice(0, 10)}</div>
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* ═══ 新增：导入/导出配置（全宽）═══ */}
            <div className="xl:col-span-3 p-3 rounded-lg bg-muted/30 border border-border space-y-2">
              <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider">
                导入/导出配置
              </p>
              <p className="text-xs 2xl:text-sm text-muted-foreground">
                备份所有 Cookie + 账号列表 + 服务 URL 为 JSON；导入时覆盖当前配置。
                <span className="text-amber-400"> 文件含敏感信息，请妥善保管。</span>
              </p>
              <div className="flex gap-2">
                <button onClick={handleExportConfig}
                  className="px-3 py-1.5 rounded text-xs font-mono bg-secondary text-secondary-foreground hover:bg-border flex items-center gap-1.5">
                  <Download size={11}/> 导出为 JSON
                </button>
                <label className="px-3 py-1.5 rounded text-xs font-mono bg-secondary text-secondary-foreground hover:bg-border flex items-center gap-1.5 cursor-pointer">
                  {importing ? <Loader2 size={11} className="animate-spin"/> : <Upload size={11}/>}
                  {importing ? '导入中...' : '从 JSON 导入'}
                  <input type="file" accept="application/json" className="hidden"
                    onChange={e => { const f = e.target.files?.[0]; if (f) handleImportConfig(f); e.target.value = '' }} />
                </label>
              </div>
            </div>

            {/* ═══ 新增：最近入库内容预览（全宽）═══ */}
            <div className="xl:col-span-3 p-3 rounded-lg bg-muted/30 border border-border space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs 2xl:text-sm font-mono text-primary font-semibold uppercase tracking-wider">
                  最近拉取内容（RawSource）
                </p>
                <button onClick={() => fetchRecentItems()}
                  disabled={recentLoading}
                  className="flex items-center gap-1 text-xs 2xl:text-sm font-mono text-primary hover:underline disabled:opacity-50">
                  {recentLoading ? <Loader2 size={10} className="animate-spin"/> : <RefreshCw size={10}/>}
                  刷新
                </button>
              </div>
              <div className="flex flex-wrap gap-1">
                {[
                  {v:'all', l:'全部'}, {v:'twitter', l:'Twitter'},
                  {v:'weibo', l:'微博'}, {v:'xiaohongshu', l:'小红书'},
                  {v:'douyin', l:'抖音'}, {v:'wechat_mp', l:'公众号'},
                ].map(p => (
                  <button key={p.v}
                    onClick={() => fetchRecentItems(p.v)}
                    className={`px-2 py-0.5 rounded text-xs 2xl:text-sm font-mono border transition-colors ${
                      recentPlatform === p.v
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'border-border bg-background hover:bg-muted/50 text-muted-foreground'
                    }`}>
                    {p.l}
                  </button>
                ))}
              </div>
              <div className="max-h-96 overflow-y-auto space-y-1.5 border border-border rounded bg-background/50 p-2">
                {recentItems.length === 0 && !recentLoading && (
                  <p className="text-xs 2xl:text-sm text-muted-foreground text-center py-4">
                    暂无内容。点击"刷新"加载最新入库数据。
                  </p>
                )}
                {recentItems.map(item => (
                  <div key={item.id} className="p-2 rounded border border-border bg-muted/20 space-y-0.5">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className={`text-[10px] 2xl:text-xs font-mono px-1 rounded ${
                            item.status === 'ingested' ? 'bg-accent/20 text-accent'
                            : item.status === 'failed' ? 'bg-destructive/20 text-destructive'
                            : 'bg-muted text-muted-foreground'
                          }`}>{item.status}</span>
                          <span className="text-[10px] 2xl:text-xs font-mono text-muted-foreground">
                            {item.source_type}
                          </span>
                          {item.author && (
                            <span className="text-[10px] 2xl:text-xs font-mono text-primary truncate max-w-[120px]">
                              @{item.author}
                            </span>
                          )}
                          <span className="text-[10px] 2xl:text-xs font-mono text-muted-foreground ml-auto">
                            {item.created_at ? new Date(item.created_at).toLocaleString('zh-CN',
                              { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' })
                              : ''}
                          </span>
                        </div>
                        <div className="text-xs 2xl:text-sm font-mono text-foreground mt-0.5 truncate" title={item.title}>
                          {item.title || '(无标题)'}
                        </div>
                        <div className="text-xs 2xl:text-sm text-muted-foreground line-clamp-2 mt-0.5">
                          {item.preview}
                        </div>
                      </div>
                      {item.url && (
                        <a href={item.url} target="_blank" rel="noopener noreferrer"
                          className="shrink-0 text-muted-foreground hover:text-primary"
                          title="打开原文">
                          <ExternalLink size={11}/>
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </section>

      {/* 测试结果 inline 提示（保留，位于顶部反馈区）*/}
      {testResult && (
        <div className={`mt-4 p-3 rounded border text-xs font-mono flex items-center gap-2 ${
          testResult.success ? 'bg-accent/10 border-accent/30 text-accent' : 'bg-destructive/10 border-destructive/30 text-destructive'
        }`}>
          {testResult.success ? <CheckCircle2 size={13}/> : <XCircle size={13}/>}
          {testResult.msg}
        </div>
      )}

      </div> {/* /max-w container */}

      {/* ════════ Sticky 底部保存栏 ════════ */}
      <div className="sticky bottom-0 left-0 right-0 z-20 border-t border-border bg-card/95 backdrop-blur-sm">
        <div className="max-w-[1400px] mx-auto px-6 py-3 flex items-center gap-3">
          <button onClick={handleSave} disabled={saving}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded text-xs font-mono font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity">
            {saving ? <Loader2 size={13} className="animate-spin"/> : <Save size={13}/>}
            {saving ? '保存中...' : '保存设置'}
          </button>
          <button onClick={handleTest} disabled={testing}
            className="flex items-center gap-2 px-4 py-2 bg-secondary text-secondary-foreground rounded text-xs font-mono hover:bg-border disabled:opacity-50 transition-colors">
            {testing ? <Loader2 size={13} className="animate-spin"/> : <RefreshCw size={13}/>}
            {testing ? '测试中...' : '测试 LLM 连接'}
          </button>
          {saved && <span className="flex items-center gap-1 text-xs font-mono text-accent"><CheckCircle2 size={13}/> 已保存</span>}
          <span className="ml-auto text-xs 2xl:text-sm font-mono text-muted-foreground hidden md:inline">
            非密钥设置会保存到本地配置 · API Key 由 SOPS 注入
          </span>
        </div>
      </div>
    </div>
  )
}
