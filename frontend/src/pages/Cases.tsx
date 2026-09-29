import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Briefcase, Plus } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Modal, Spinner, Textarea } from '../components/ui'
import { api } from '../lib/api'
import { fmtDate, fmtNumber } from '../lib/format'
import { useI18n } from '../lib/i18n'

export function Cases() {
  const { t, lang } = useI18n()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const cases = useQuery({ queryKey: ['cases'], queryFn: api.cases })
  const create = useMutation({
    mutationFn: () => api.createCase(title, description),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ['cases'] })
      navigate(`/cases/${c.id}`)
    },
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold">{t('cases_title')}</h1>
        <Button variant="primary" onClick={() => setOpen(true)}>
          <Plus className="size-4" />
          {t('new_case')}
        </Button>
      </div>
      {cases.isPending ? (
        <Spinner />
      ) : cases.isError ? (
        <ErrorBox error={cases.error} onRetry={() => cases.refetch()} />
      ) : cases.data.length === 0 ? (
        <Card>
          <Empty>{t('no_cases')}</Empty>
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {cases.data.map((c) => (
            <Link key={c.id} to={`/cases/${c.id}`} className="flex flex-col gap-2 rounded-xl border border-line bg-surface p-4 hover:border-accent">
              <div className="flex items-start justify-between gap-2">
                <span className="flex items-center gap-2 font-bold">
                  <Briefcase className="size-4 shrink-0 text-accent" />
                  {c.title}
                </span>
                <Badge tone={c.status === 'open' ? 'accent' : 'neutral'}>{t(c.status)}</Badge>
              </div>
              {c.description && <p className="line-clamp-2 text-sm text-ink-2">{c.description}</p>}
              <div className="mt-auto text-xs text-muted">
                {fmtNumber(c.item_count, lang)} {t('items')} · {fmtDate(c.updated_at, lang)}
              </div>
            </Link>
          ))}
        </div>
      )}
      <Modal open={open} onClose={() => setOpen(false)} title={t('new_case')}>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate()
          }}
        >
          <Field label={t('case_title')}>
            <Input required autoFocus value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} />
          </Field>
          <Field label={t('description')}>
            <Textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          {create.error && <ErrorBox error={create.error} />}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              {t('cancel')}
            </Button>
            <Button type="submit" variant="primary" loading={create.isPending}>
              {t('create')}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
