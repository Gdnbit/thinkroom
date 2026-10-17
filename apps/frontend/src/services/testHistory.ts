/**
 * 测试历史记录 — localStorage 持久化
 * 保存全链路测试、接口面板、V2 专项、解析质量、系统状态的每次运行结果。
 *
 * 存储策略：
 * - 最多 200 条记录
 * - 总体积不超过 4MB（localStorage 上限约 5-10MB，留余量给其他数据）
 * - 超限时优先删除最旧记录
 * - 单条 details 超 50KB 自动截断日志文本
 */

const STORAGE_KEY = 'verification_test_history'
const MAX_RECORDS = 200
const MAX_BYTES = 4 * 1024 * 1024 // 4MB

export type TestCategory = 'suite' | 'api' | 'v2' | 'ingestion' | 'status' | 'master'

export interface TestHistoryRecord {
  id: string
  category: TestCategory
  label: string           // 简短标签，如 "全链路一键测试"
  timestamp: string       // ISO 时间
  durationMs: number
  summary: {
    total: number
    passed: number
    failed: number
    warnings?: number
  }
  /** 完整日志文本，用于展开查看 */
  logText: string
  /** 详细结果数据（JSON 序列化安全） */
  details?: any
}

/** 生成唯一 ID */
function uid(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

/** 安全保存到 localStorage，处理超限 */
function safePersist(records: TestHistoryRecord[]): void {
  let data = records
  // 条数限制
  if (data.length > MAX_RECORDS) data = data.slice(0, MAX_RECORDS)
  let json = JSON.stringify(data)
  // 体积限制：逐步移除最旧记录直到满足
  while (json.length > MAX_BYTES && data.length > 1) {
    data = data.slice(0, -1)
    json = JSON.stringify(data)
  }
  // 仍然超限（单条极大），截断日志
  if (json.length > MAX_BYTES && data.length === 1) {
    data[0] = { ...data[0], logText: data[0].logText.slice(0, 2000) + '\n…(已截断)', details: undefined }
    json = JSON.stringify(data)
  }
  try {
    localStorage.setItem(STORAGE_KEY, json)
  } catch {
    // 最后手段：只保留最新 20 条的摘要
    const slim = data.slice(0, 20).map(r => ({ ...r, logText: r.logText.slice(0, 500), details: undefined }))
    localStorage.setItem(STORAGE_KEY, JSON.stringify(slim))
  }
}

/** 读取所有历史记录 */
export function loadHistory(): TestHistoryRecord[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    return JSON.parse(raw) as TestHistoryRecord[]
  } catch {
    return []
  }
}

/** 按 ID 获取单条记录 */
export function getHistoryById(id: string): TestHistoryRecord | undefined {
  return loadHistory().find(r => r.id === id)
}

/** 保存一条记录 */
export function saveHistory(record: Omit<TestHistoryRecord, 'id'>): string {
  const all = loadHistory()
  const id = uid()
  const entry: TestHistoryRecord = { id, ...record }
  all.unshift(entry) // 最新在前
  safePersist(all)
  return id
}

/** 删除一条记录 */
export function deleteHistory(id: string): void {
  const all = loadHistory().filter(r => r.id !== id)
  safePersist(all)
}

/** 清空所有记录 */
export function clearHistory(): void {
  localStorage.removeItem(STORAGE_KEY)
}

/** 清空某个类别的所有记录 */
export function clearCategoryHistory(category: TestCategory): void {
  const all = loadHistory().filter(r => r.category !== category)
  safePersist(all)
}

/** 按类别筛选 */
export function filterHistory(category?: TestCategory): TestHistoryRecord[] {
  const all = loadHistory()
  if (!category) return all
  return all.filter(r => r.category === category)
}

/** 获取存储用量信息 */
export function getStorageInfo(): { count: number; sizeKB: number; maxKB: number } {
  const raw = localStorage.getItem(STORAGE_KEY) || ''
  return { count: loadHistory().length, sizeKB: Math.round(raw.length / 1024), maxKB: Math.round(MAX_BYTES / 1024) }
}
