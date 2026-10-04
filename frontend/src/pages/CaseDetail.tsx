import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FileText, Plus, Route, StickyNote, Trash2, Wallet } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Address, TxLink } from '../components/Address'
import { ChainBadge } from '../components/badges'
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Spinner, Textarea } from '../components/ui'
import { api, type CaseItem } from '../lib/api'
import { fmtAmount, fmtDate } from '../lib/format'
import { useI18n } from '../lib/i18n'
import { Worth } from '../lib/money'

export function CaseDetail() {
  const id = Number(useParams().id)
  const { t, lang } = useI18n()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const q = useQuery({ queryKey: ['case', id], queryFn: () => api.case(id) })
  const [address, setAddress] = useState('')
  const [noteTitle, setNoteTitle] = useState('')
  const [note, setNote] = useState('')
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['case', id] })
    qc.invalidateQueries({ queryKey: ['cases'] })
  }

  const addAddress = useMutation({
    mutationFn: () => api.addCaseItem(id, { kind: 'address', address: address.trim() }),
    onSuccess: () => {
      setAddress('')
      invalidate()
    },
  })
  const addNote = useMutation({
    mutationFn: () => api.addCaseItem(id, { kind: 'note', title: noteTitle, note }),
    onSuccess: () => {
      setNote('')
      setNoteTitle('')
      invalidate()
    },
  })
  const removeItem = useMutation({ mutationFn: (itemId: number) => api.deleteCaseItem(id, itemId), onSuccess: invalidate })
  const toggle = useMutation({
    mutationFn: () => api.updateCase(id, { status: q.data?.status === 'open' ? 'closed' : 'open' }),
    onSuccess: invalidate,
  })
  const remove = useMutation({
    mutationFn: () => api.deleteCase(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cases'] })
      navigate('/cases')
    },
  })

  if (q.isPending) return <Spinner />
  if (q.isError) return <ErrorBox error={q.error} onRetry={() => q.refetch()} />
  const c = q.data
  const byKind = (k: CaseItem['kind']) => c.items.filter((i) => i.kind === k)

  const del = (item: CaseItem) => (
    <button
      className="rounded p-1 text-muted hover:bg-critical/10 hover:text-critical"
      title={t('delete')}
      onClick={() => confirm(t('confirm_delete')) && removeItem.mutate(item.id)}
    >
      <Trash2 className="size-4" />
    </button>
  )

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-bold">{c.title}</h1>
          <Badge tone={c.status === 'open' ? 'accent' : 'neutral'}>{t(c.status)}</Badge>
        </div>
        {c.description && <p className="whitespace-pre-wrap text-sm text-ink-2">{c.description}</p>}
        <div className="flex flex-wrap gap-2">
          <a href={`/api/cases/${id}/report?lang=${lang}`} target="_blank" rel="noreferrer">
            <Button size="sm" tabIndex={-1}>
              <FileText className="size-3.5" />
              {t('report')}
            </Button>
          </a>
          <a href={`/api/cases/${id}/export.json`}>
            <Button size="sm" tabIndex={-1}>
              <Download className="size-3.5" />
              {t('export_json')}
            </Button>
          </a>
          <Button size="sm" onClick={() => toggle.mutate()} loading={toggle.isPending}>
            {c.status === 'open' ? t('close_case') : t('reopen')}
          </Button>
          <Button size="sm" variant="danger" onClick={() => confirm(t('confirm_delete')) && remove.mutate()}>
            <Trash2 className="size-3.5" />
            {t('delete')}
          </Button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`${t('addresses')} (${byKind('address').length})`}>
          <form
            className="mb-3 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              addAddress.mutate()
            }}
          >
            <Input className="mono" placeholder={t('address')} value={address} onChange={(e) => setAddress(e.target.value)} required />
            <Button type="submit" loading={addAddress.isPending}>
              <Plus className="size-4" />
            </Button>
          </form>
          {addAddress.error && <ErrorBox error={addAddress.error} />}
          {byKind('address').length === 0 ? (
            <Empty />
          ) : (
            <ul className="flex flex-col divide-y divide-[var(--border)]">
              {byKind('address').map((item) => (
                <li key={item.id} className="flex items-start justify-between gap-2 py-2">
                  <div className="flex min-w-0 flex-col gap-1">
                    <span className="flex items-center gap-2">
                      <Wallet className="size-4 text-accent" />
                      {item.chain && <ChainBadge chain={item.chain} />}
                    </span>
                    {item.chain && item.address && <Address address={item.address} chain={item.chain} />}
                    {item.note && <span className="text-sm text-ink-2">{item.note}</span>}
                  </div>
                  {del(item)}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title={`${t('notes')} (${byKind('note').length})`}>
          <form
            className="mb-3 flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              addNote.mutate()
            }}
          >
            <Field label={t('case_title')}>
              <Input value={noteTitle} onChange={(e) => setNoteTitle(e.target.value)} />
            </Field>
            <Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} required placeholder={t('note')} />
            <div>
              <Button type="submit" size="sm" loading={addNote.isPending}>
                <StickyNote className="size-3.5" />
                {t('add_note')}
              </Button>
            </div>
          </form>
          <ul className="flex flex-col gap-2">
            {byKind('note').map((item) => (
              <li key={item.id} className="flex items-start justify-between gap-2 rounded-lg bg-surface-2 p-3">
                <div className="min-w-0">
                  {item.title && <div className="font-bold">{item.title}</div>}
                  <p className="whitespace-pre-wrap text-sm">{item.note}</p>
                  <div className="mt-1 text-xs text-muted">{fmtDate(item.created_at, lang)}</div>
                </div>
                {del(item)}
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title={`${t('traces')} (${byKind('trace').length})`}>
        {byKind('trace').length === 0 ? (
          <Empty>
            <Link to="/trace" className="text-accent hover:underline">
              {t('nav_trace')}
            </Link>
          </Empty>
        ) : (
          <ul className="flex flex-col divide-y divide-[var(--border)]">
            {byKind('trace').map((item) => {
              const d = item.data
              return (
                <li key={item.id} className="flex items-start justify-between gap-2 py-3">
                  <div className="flex min-w-0 flex-col gap-1 text-sm">
                    <span className="flex items-center gap-2 font-bold">
                      <Route className="size-4 text-accent" />
                      {item.title}
                    </span>
                    {d && (
                      <>
                        <TxLink hash={d.start.tx_hash} chain={d.chain} />
                        <span className="text-ink-2">
                          {fmtAmount(d.traced_amount, lang)} {d.token_symbol} → {d.endpoints.length} {t('endpoints')}
                        </span>
                        <Worth amount={d.traced_amount} symbol={d.token_symbol} contract={d.token_contract} chain={d.chain} at={d.start.timestamp} />
                        <Link
                          to={`/trace?chain=${d.chain}&address=${encodeURIComponent(d.start.from_address)}&tx=${encodeURIComponent(d.start.tx_hash)}&direction=${d.direction}`}
                          className="text-xs text-accent hover:underline"
                        >
                          {t('run_trace')} ↻
                        </Link>
                      </>
                    )}
                    {item.note && <span className="text-ink-2">{item.note}</span>}
                  </div>
                  {del(item)}
                </li>
              )
            })}
          </ul>
        )}
      </Card>
    </div>
  )
}
