/* 学生端「老师分享」全屏预览（批次 A5 消费者模式）——只能看，不能改。
 * 取数两路：/api/artifacts/shared/{id}（分享记录放行，S3 工件卡）→
 * 失败回退 /api/artifacts/{id}（owner=本人，批 2 S5b：表述特训报告等自有工件）。 */
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../lib/api'

interface SharedMeta {
  artifact_id: string
  kind: string
  title: string
  current_version: number
}

export function StudentArtifactPage() {
  const { artifactId } = useParams<{ artifactId: string }>()
  const navigate = useNavigate()
  const [meta, setMeta] = useState<SharedMeta | null>(null)
  const [frameSrc, setFrameSrc] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!artifactId) return
    const token = localStorage.getItem('token') || ''
    const loadVia = (base: 'shared' | 'own') => {
      const metaUrl = base === 'shared'
        ? `/api/artifacts/shared/${artifactId}`
        : `/api/artifacts/${artifactId}`
      const fileUrl = base === 'shared'
        ? `/api/artifacts/shared/${artifactId}/v/`
        : `/api/artifacts/${artifactId}/v/`
      return api.get<SharedMeta>(metaUrl).then((d) => {
        setMeta(d)
        return fetch(`${fileUrl}${d.current_version}`,
              { headers: { Authorization: `Bearer ${token}` } })
          .then((resp) => {
            if (!resp.ok) throw new Error(String(resp.status))
            return resp.blob()
          })
          .then((blob) => setFrameSrc(URL.createObjectURL(blob)))
      })
    }
    loadVia('shared')
      .catch(() => loadVia('own'))
      .catch(() => setError('这份内容不存在或已撤回。'))
  }, [artifactId])

  return (
    <div className="h-full flex flex-col bg-white">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <button
          onClick={() => navigate(-1)}
          aria-label="返回"
          className="p-1.5 rounded-[var(--radius-sm)] text-ink-secondary hover:bg-surface-muted cursor-pointer"
        >
          <ArrowLeft size={16} />
        </button>
        <span className="text-sm font-medium text-ink truncate">
          {meta?.title || '老师分享的工件'}
        </span>
      </div>
      {error ? (
        <div className="flex-1 flex items-center justify-center text-sm text-ink-muted px-6 text-center">
          {error}
        </div>
      ) : frameSrc ? (
        <iframe
          src={frameSrc}
          sandbox="allow-scripts"
          title={meta?.title || '工件预览'}
          className="flex-1 w-full border-0"
        />
      ) : (
        <div className="flex-1 flex items-center justify-center text-sm text-ink-muted">加载中…</div>
      )}
    </div>
  )
}
