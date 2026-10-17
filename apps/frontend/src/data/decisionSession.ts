export type IngestionStageState = 'done' | 'active' | 'pending'

export interface DecisionFile {
  name: string
  type: string
  size: string
  status: 'stored' | 'processing' | 'pending'
}

export interface IngestionStage {
  title: string
  description: string
  state: IngestionStageState
}

export interface EvidenceItem {
  title: string
  source: string
  strength: 'strong' | 'medium' | 'weak'
  summary: string
}

export interface RiskItem {
  title: string
  level: 'high' | 'medium' | 'low'
  mitigation: string
}

export interface DecisionSession {
  id: string
  title: string
  userQuestion: string
  createdAt: string
  files: DecisionFile[]
  ingestionStages: IngestionStage[]
  answer: {
    conclusion: string
    rationale: string
    actionLogic: string
    limitation: string
  }
  evidence: EvidenceItem[]
  risks: RiskItem[]
  snapshot: {
    status: 'draft' | 'saved'
    version: string
    confidence: string
  }
  review: {
    status: 'not_started' | 'active' | 'done'
    nextStep: string
  }
}

export const currentDecisionSession: DecisionSession = {
  id: 'empty-session',
  title: '当前会话尚未接收资料',
  userQuestion: '',
  createdAt: '',
  files: [],
  ingestionStages: [
    { title: '等待接收', description: '拖入文件或直接提问后开始当前决策任务', state: 'active' },
    { title: '等待解析', description: '系统会提取文本、表格、图片和元数据', state: 'pending' },
    { title: '等待切片', description: '资料会按主题、来源和证据颗粒度组织', state: 'pending' },
    { title: '等待向量化', description: '完成后写入可检索证据索引', state: 'pending' },
    { title: '等待引用', description: '入库完成后可被问答、审计和复盘引用', state: 'pending' },
  ],
  answer: {
    conclusion: '尚未形成结论。',
    rationale: '当前会话还没有可引用资料或回答记录。',
    actionLogic: '上传资料或提出问题后，系统会基于当前会话的资料源生成回答、引用和风险说明。',
    limitation: '空状态不代表任何投资或操作建议。',
  },
  evidence: [],
  risks: [],
  snapshot: {
    status: 'draft',
    version: 'empty',
    confidence: '尚未评估',
  },
  review: {
    status: 'not_started',
    nextStep: '先在决策问答中上传资料或提出问题。',
  },
}
