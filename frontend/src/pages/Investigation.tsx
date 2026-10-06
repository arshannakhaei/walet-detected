import { useMutation, useQuery } from '@tanstack/react-query'
import { Download, ExternalLink, FileText, Play } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Badge, Button, Card, ErrorBox, Field, Input, Spinner, Textarea } from '../components/ui'
import { api, type InvestigationJob } from '../lib/api'
import { useI18n, type TKey } from '../lib/i18n'
import { readJson, writeJson } from '../lib/storage'

const STORE = 'chaintrace.investigation'
const STAGES: Record<string, TKey> = {
  histories: 'inv_stage_histories',
  labels: 'inv_stage_labels',
  links: 'inv_stage_links',
  profiles: 'inv_stage_profiles',
  verification: 'inv_stage_verification',
  rendering: 'inv_stage_rendering',
}

function count(text: string): number {
  return new Set(text.split(/[\s,;]+/).filter(Boolean)).size
}

export function Investigation() {
  const { t } = useI18n()
  const [params, setParams] = useSearchParams()
  const saved = readJson<{ focus: string; addresses: string; title: string; caseDate: string }>(STORE, {
    focus: '',
    addresses: '',
    title: '',
    caseDate: '',
  })
  const [focus, setFocus] = useState(saved.focus)
  const [addresses, setAddresses] = useState(saved.addresses)
  const [title, setTitle] = useState(saved.title)
  const [caseDate, setCaseDate] = useState(saved.caseDate)
  const jobId = params.get('job')

  useEffect(() => {
    writeJson(STORE, { focus, addresses, title, caseDate })
  }, [focus, addresses, title, caseDate])

  const start = useMutation({
    mutationFn: () =>
      api.startInvestigation({ focus, addresses, token: 'USDT', title: title || null, case_date: caseDate || null }),
    onSuccess: (job) => setParams({ job: job.id }, { replace: true }),
  })
  const job = useQuery({
    queryKey: ['investigation', jobId],
    queryFn: () => api.investigationJob(jobId!),
    enabled: !!jobId,
    retry: false,
    refetchInterval: (q) => ((q.state.data as InvestigationJob | undefined)?.state === 'running' ? 1500 : false),
  })
  const data = job.data
  const running = start.isPending || data?.state === 'running'
  const percent = data && data.total > 0 ? Math.round((100 * data.done) / data.total) : 0

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h1 className="text-xl font-bold text-ink">{t('inv_title')}</h1>
        <p className="mt-1 text-sm text-ink-2">{t('inv_intro')}</p>
      </div>
      <Card>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            start.mutate()
          }}
        >
          <Field label={`${t('inv_focus')} (${count(focus)})`}>
            <Textarea dir="ltr" rows={3} value={focus} onChange={(e) => setFocus(e.target.value)} placeholder="T..." className="font-mono text-xs" />
          </Field>
          <Field label={`${t('inv_list')} (${count(addresses)})`}>
            <Textarea dir="ltr" rows={8} value={addresses} onChange={(e) => setAddresses(e.target.value)} placeholder="T...&#10;T..." className="font-mono text-xs" />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={t('inv_report_title')}>
              <Input value={title} onChange={(e) => setTitle(e.target.value)} />
            </Field>
            <Field label={t('inv_case_date')}>
              <Input value={caseDate} onChange={(e) => setCaseDate(e.target.value)} />
            </Field>
          </div>
          <p className="text-xs text-muted">{t('inv_hint')}</p>
          <Button type="submit" variant="primary" disabled={running || count(focus) < 1 || count(addresses) + count(focus) < 2}>
            <Play className="size-4" /> {t('inv_run')}
          </Button>
        </form>
      </Card>

      {start.isError && <ErrorBox error={start.error} />}
      {job.isError && <ErrorBox error={job.error} />}

      {data?.state === 'running' && (
        <Card>
          <Spinner label={`${t(STAGES[data.stage] ?? 'inv_stage_histories')} — ${data.done} / ${data.total}`} />
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${percent}%` }} />
          </div>
        </Card>
      )}
      {data?.state === 'failed' && <ErrorBox error={new Error(data.error ?? 'failed')} />}
      {data?.state === 'done' && (
        <Card title={<span className="flex items-center gap-2"><FileText className="size-4" /> {t('inv_ready')}</span>}>
          <div className="flex flex-wrap gap-2">
            <a href={data.report_url ?? '#'} target="_blank" rel="noreferrer">
              <Button type="button" variant="primary"><ExternalLink className="size-4" /> {t('inv_open')}</Button>
            </a>
            <a href={data.zip_url ?? '#'} download>
              <Button type="button" variant="secondary"><Download className="size-4" /> {t('inv_zip')}</Button>
            </a>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-ink-2">
            <span>{t('inv_verification')}:</span>
            {Object.entries(data.verification).map(([wallet, status]) => (
              <Badge key={wallet} tone={status === 'verified' ? 'good' : status === 'mismatch' ? 'critical' : 'warning'}>
                <bdi dir="ltr">{wallet}</bdi> {t(status === 'verified' ? 'inv_verified' : status === 'mismatch' ? 'inv_mismatch' : 'inv_unavailable')}
              </Badge>
            ))}
          </div>
          {data.warnings.length > 0 && (
            <ul className="mt-3 list-disc space-y-1 ps-5 text-xs text-muted" dir="ltr">
              {data.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  )
}
