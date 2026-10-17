import { useState, useCallback } from 'react'
import { Upload, FileText, CheckCircle2, AlertCircle, Loader2, X, Layers } from 'lucide-react'
import { clsx } from 'clsx'
import { useUpload } from '@/services/hooks'
import { uploadApi, type IngestionProgress, type IngestionStatus } from '@/services/upload'

interface FileItem {
  file: File
  status: 'pending' | 'uploading' | 'done' | 'error'
  message?: string
  sourceId?: string
  jobId?: string
}

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

function summarizeInboxProgress(progress: IngestionProgress[], statuses: IngestionStatus[]) {
  const total = Math.max(statuses.length, progress.length)
  const completed = statuses.filter(s => s.status === 'completed').length
  const failed = statuses.filter(s => s.status === 'failed').length
  const running = progress.find(p => p.status === 'running')
  const pctValues = progress.map(p => Number(p.progress_pct || 0)).filter(Number.isFinite)
  const percent = pctValues.length ? Math.max(...pctValues) : (total ? Math.round((completed / total) * 100) : 0)
  const stage = running?.current_stage_label || running?.message || '后台解析中'
  const elapsedMs = running?.current_stage_elapsed_ms ?? running?.elapsed_ms
  const elapsedText = typeof elapsedMs === 'number' && elapsedMs > 0 ? `，当前环节 ${Math.round(elapsedMs / 1000)}s` : ''

  if (failed > 0 && completed + failed >= total) {
    return { type: 'error' as const, message: `解析完成 ${completed}/${total}，失败 ${failed}` }
  }
  if (total > 0 && completed >= total) {
    return { type: 'done' as const, message: `${completed} 份资料已解析、向量化并入库` }
  }
  return {
    type: 'uploading' as const,
    message: `${stage}，进度 ${Math.min(99, percent)}%${elapsedText}`,
  }
}

export default function Inbox() {
  const [files, setFiles] = useState<FileItem[]>([])
  const [speakerHint, setSpeakerHint] = useState('')
  const [themeHint, setThemeHint] = useState('')
  const [mergeImages, setMergeImages] = useState(false)
  const [dragging, setDragging] = useState(false)
  const uploadMutation = useUpload()

  const addFiles = (incoming: File[]) => {
    const newItems = incoming.map(f => ({ file: f, status: 'pending' as const }))
    setFiles(p => [...p, ...newItems])
  }

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    addFiles(Array.from(e.dataTransfer.files))
  }, [])

  const onFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) addFiles(Array.from(e.target.files))
    e.target.value = ''
  }

  const removeFile = (idx: number) => {
    setFiles(p => p.filter((_, i) => i !== idx))
  }

  const handleUpload = async () => {
    const pending = files.filter(f => f.status === 'pending')
    if (!pending.length) return

    setFiles(p => p.map(f => f.status === 'pending' ? { ...f, status: 'uploading' } : f))

    try {
      const result = await uploadMutation.mutateAsync({
        files: pending.map(f => f.file),
        speakerHint: speakerHint || undefined,
        themeHint: themeHint || undefined,
        mergeImages,
      })
      setFiles(p => p.map(f =>
        f.status === 'uploading' ? { ...f, status: 'done', message: '上传成功，正在触发 AI 抽取…' } : f
      ))
      if (result.source_ids && result.source_ids.length > 0) {
        setFiles(p => p.map(f =>
          f.status === 'done' ? { ...f, message: '已保存，正在创建后台解析任务…' } : f
        ))
        const queued = await uploadApi.processSelectedSources(result.source_ids, true)
        const jobIds = [
          ...((queued.data.job_ids || []).filter((jobId) => typeof jobId === 'string' && jobId)),
          ...((queued.data.jobs || []).flatMap((item) => {
            const jobId = item.job_id
            return typeof jobId === 'string' && jobId ? [jobId] : []
          })),
        ].filter((jobId, index, list) => list.indexOf(jobId) === index)
        if (result.source_ids.length > 0 && !jobIds.length) {
          setFiles(p => p.map(f =>
            f.status === 'done' ? { ...f, status: 'error', message: '后台解析任务创建失败，请稍后重试' } : f
          ))
          return
        }
        setFiles(p => p.map((f, index) => {
          if (f.status !== 'done') return f
          const sourceId = result.source_ids[index]
          const jobId = jobIds[index]
          return {
            ...f,
            sourceId,
            jobId,
            message: jobId ? `后台解析已排队：${jobId}` : '后台解析已提交',
          }
        }))

        const maxPolls = 40
        let finished = false
        for (let attempt = 0; attempt < maxPolls; attempt += 1) {
          await sleep(attempt === 0 ? 500 : 2500)
          const [progressResults, statuses] = await Promise.all([
            Promise.allSettled(jobIds.map(jobId => uploadApi.getProgress(jobId))),
            uploadApi.getBatchStatus(result.source_ids).catch(() => [] as IngestionStatus[]),
          ])
          const progress = progressResults.flatMap(item => item.status === 'fulfilled' ? [item.value] : [])
          const summary = summarizeInboxProgress(progress, statuses)
          setFiles(p => p.map(f => {
            if (f.status !== 'done') return f
            return {
              ...f,
              status: summary.type === 'error' ? 'error' : 'done',
              message: summary.message,
            }
          }))
          if (summary.type !== 'uploading') {
            finished = true
            break
          }
        }
        if (!finished) {
          setFiles(p => p.map(f =>
            f.status === 'done' ? { ...f, message: '后台仍在解析中，可稍后到资料与来源查看进度' } : f
          ))
        }
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Upload failed'
      setFiles(p => p.map(f =>
        f.status === 'uploading' ? { ...f, status: 'error', message: msg } : f
      ))
    }
  }

  const pendingCount = files.filter(f => f.status === 'pending').length
  const imageCount = files.filter(f => f.status === 'pending' && f.file.type.startsWith('image/')).length

  return (
    <div className="p-6 2xl:p-8 max-w-[900px] 2xl:max-w-[1100px] mx-auto">
      <div className="page-header !mb-5 !pb-4">
        <span className="section-label">Inbox</span>
        <h1 className="text-2xl font-semibold text-foreground">上传台</h1>
        <p className="text-sm text-muted-foreground mt-1">上传文章、报告、访谈截图，系统将自动提取文字并抽取观点</p>
      </div>

      {/* 拖放区 */}
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={clsx(
          'border-2 border-dashed rounded-xl p-10 flex flex-col items-center gap-3 transition-colors cursor-pointer',
          dragging ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/50'
        )}
        onClick={() => document.getElementById('file-input')?.click()}
      >
        <Upload size={28} className="text-muted-foreground" />
        <p className="text-sm text-muted-foreground">拖拽文件到此处，或点击选择</p>
        <p className="text-xs text-muted-foreground">支持 PDF、Word(.docx)、TXT、图片（JPG/PNG/WebP），最大 20MB</p>
        <input id="file-input" type="file" multiple className="hidden"
          accept=".pdf,.txt,.jpg,.jpeg,.png,.webp,.docx,.doc" onChange={onFileInput} />
      </div>

      {/* 元信息 */}
      <div className="grid grid-cols-2 gap-3 mt-4">
        <input value={speakerHint} onChange={e => setSpeakerHint(e.target.value)}
          placeholder="来源（如：达利欧）"
          className="bg-secondary border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary" />
        <input value={themeHint} onChange={e => setThemeHint(e.target.value)}
          placeholder="主题（如：流动性 宏观）"
          className="bg-secondary border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary" />
      </div>

      {/* 多图合并开关 */}
      {imageCount > 1 && (
        <div className="mt-3 flex items-center gap-3 p-3 rounded-lg bg-card border border-border">
          <Layers size={15} className="text-primary shrink-0" />
          <div className="flex-1">
            <p className="text-xs font-mono text-foreground">多图自动拼接</p>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              开启后将 {imageCount} 张图片按顺序提取文字后合并为一份资料，适用于长截图切成多份、聊天记录分段截图
            </p>
          </div>
          <button
            onClick={() => setMergeImages(v => !v)}
            className={`relative w-9 h-5 rounded-full transition-colors shrink-0 ${
              mergeImages ? 'bg-primary' : 'bg-border'
            }`}
          >
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
              mergeImages ? 'translate-x-4' : 'translate-x-0'
            }`} />
          </button>
        </div>
      )}

      {/* 文件列表 */}
      {files.length > 0 && (
        <div className="mt-4 space-y-2">
          {files.map((item, i) => (
            <div key={i} className="flex items-center gap-3 bg-card border border-border rounded-lg px-3 py-2">
              <FileText size={16} className="text-muted-foreground shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-foreground truncate">{item.file.name}</p>
                {item.message && <p className="text-xs text-muted-foreground truncate">{item.message}</p>}
              </div>
              <div className="shrink-0">
                {item.status === 'pending' && (
                  <button onClick={() => removeFile(i)} className="text-muted-foreground hover:text-foreground">
                    <X size={14} />
                  </button>
                )}
                {item.status === 'uploading' && <Loader2 size={14} className="animate-spin text-primary" />}
                {item.status === 'done' && <CheckCircle2 size={14} className="text-emerald-400" />}
                {item.status === 'error' && <AlertCircle size={14} className="text-red-400" />}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 上传按钮 */}
      {pendingCount > 0 && (
        <button onClick={handleUpload} disabled={uploadMutation.isPending}
          className="mt-4 w-full bg-primary text-primary-foreground rounded-lg py-2.5 text-sm font-medium disabled:opacity-50 hover:opacity-90 transition-opacity">
          {uploadMutation.isPending
            ? '上传中...'
            : `上传 ${pendingCount} 个文件${
                mergeImages && imageCount > 1 ? `（${imageCount} 张图片将合并）` : ''
              }`
          }
        </button>
      )}
    </div>
  )
}
