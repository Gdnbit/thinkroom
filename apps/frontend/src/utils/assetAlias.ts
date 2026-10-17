/**
 * A1 · 资产识别与别名统一（前端镜像）
 *
 * 与后端 app/utils/asset_alias.py 保持同构。两处任一更新时应同步修改。
 */

export const ASSET_ALIASES: Record<string, string> = {
  // 加密货币
  btc: 'BTC', bitcoin: 'BTC', '比特币': 'BTC',
  eth: 'ETH', ethereum: 'ETH', '以太坊': 'ETH', '以太': 'ETH',
  sol: 'SOL', solana: 'SOL', '索拉纳': 'SOL',
  bnb: 'BNB', 'binance coin': 'BNB', '币安币': 'BNB',
  xrp: 'XRP', ripple: 'XRP', '瑞波': 'XRP',
  // 贵金属
  gold: 'GOLD', '黄金': 'GOLD', xau: 'GOLD',
  silver: 'SILVER', '白银': 'SILVER', xag: 'SILVER',
  // 能源
  oil: 'OIL', crude: 'OIL', wti: 'OIL', brent: 'OIL',
  '石油': 'OIL', '原油': 'OIL',
  // 美股指数
  spx: 'SPX', 's&p': 'SPX', sp500: 'SPX', '标普': 'SPX', '标普500': 'SPX', '美股': 'SPX',
  ndx: 'NDX', nasdaq: 'NDX', '纳指': 'NDX', '纳斯达克': 'NDX',
  dow: 'DJI', '道琼斯': 'DJI',
  // 宏观
  dxy: 'DXY', '美元指数': 'DXY',
  vix: 'VIX', '恐慌指数': 'VIX', '恐慌': 'VIX',
}

/** 从自由文本中识别所有资产，按首次出现位置排序。长关键词优先以避免子串误匹配。 */
export function detectAssets(text: string | null | undefined): string[] {
  if (!text) return []
  const q = text.toLowerCase()
  const sortedKeys = Object.keys(ASSET_ALIASES).sort((a, b) => b.length - a.length)
  const claimed: Array<{ start: number; end: number; sym: string }> = []
  for (const kw of sortedKeys) {
    let from = 0
    while (true) {
      const pos = q.indexOf(kw, from)
      if (pos < 0) break
      const end = pos + kw.length
      // 若与已认领区间重叠，跳过（被长词覆盖）
      const overlap = claimed.some(c => !(end <= c.start || pos >= c.end))
      if (overlap) { from = pos + 1; continue }
      claimed.push({ start: pos, end, sym: ASSET_ALIASES[kw] })
      from = end
    }
  }
  claimed.sort((a, b) => a.start - b.start)
  const seen = new Set<string>()
  const result: string[] = []
  for (const c of claimed) {
    if (!seen.has(c.sym)) { seen.add(c.sym); result.push(c.sym) }
  }
  return result
}

/** 从自由文本中识别首个资产，未匹配时返回 default。 */
export function detectAsset(text: string | null | undefined, fallback = 'BTC'): string {
  const hits = detectAssets(text)
  return hits[0] ?? fallback
}

/** 规范化用户输入的任意写法为规范符号；无法识别时返回大写原值。 */
export function canonicalize(symbol: string | null | undefined): string | null {
  if (!symbol) return null
  const s = symbol.trim()
  if (!s) return null
  const hit = ASSET_ALIASES[s.toLowerCase()]
  return hit ?? s.toUpperCase()
}
