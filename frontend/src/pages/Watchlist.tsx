import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { ArrowDownLeft, ArrowUpRight, CheckCheck, Plus, RefreshCw, Send, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Address, TxLink } from '../components/Address'
import { ChainBadge } from '../components/badges'
import { Button, Card, Empty, ErrorBox, Field, Input, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { fmtAmount, fmtDate } from '../lib/format'
import { useI18n } from '../lib/i18n'
import { Worth } from '../lib/money'

export function Watchlist() {
  const { t, lang } = useI18n()
  const qc = useQueryClient()
  const [form, setForm] = useState({ address: '', name: '', min_amount: '', token: '' })
  const watches = useQuery({ queryKey: ['watchlist'], queryFn: api.watchlist })
  const alerts = useQuery({ queryKey: ['alerts', 'all'], queryFn: () => api.alerts(false), refetchInterval: 30_000 })
  const refreshAlerts = () => qc.invalidateQueries({ queryKey: ['alerts'] })

  const add = useMutation({
    mutationFn: () =>
      api.addWatch({ address: form.address.trim(), name: form.name, min_amount: form.min_amount || '0', token: form.token || null }),
    onSuccess: () => {
      setForm({ address: '', name: '', min_amount: '', token: '' })
      qc.invalidateQueries({ queryKey: ['watchlist'] })
    },
  })
  const remove = useMutation({ mutationFn: api.removeWatch, onSuccess: () => qc.invalidateQueries({ queryKey: ['watchlist'] }) })
  const check = useMutation({ mutationFn: api.checkWatchlist, onSuccess: refreshAlerts })
  const readAll = useMutation({ mutationFn: () => api.markAlertsRead(), onSuccess: refreshAlerts })

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold">{t('watch_title')}</h1>
        <p className="mt-1 text-sm text-ink-2">{t('watch_intro')}</p>
      </div>

      <Card title={t('add_watch')}>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            add.mutate()
          }}
        >
          <Field label={t('address')} className="min-w-64 flex-1">
            <Input className="mono" required value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
          </Field>
          <Field label={t('name')} className="w-40">
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label={t('min_amount')} className="w-28">
            <Input inputMode="decimal" value={form.min_amount} onChange={(e) => setForm({ ...form, min_amount: e.target.value })} />
          </Field>
          <Field label={t('token')} className="w-28">
            <Input value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} placeholder={t('all')} />
          </Field>
          <Button type="submit" variant="primary" loading={add.isPending}>
            <Plus className="size-4" />
            {t('add_watch')}
          </Button>
        </form>
        {add.error && <div className="mt-3"><ErrorBox error={add.error} /></div>}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title={`${t('nav_watchlist')} (${watches.data?.length ?? 0})`}
          actions={
            <Button size="sm" onClick={() => check.mutate()} loading={check.isPending}>
              <RefreshCw className="size-3.5" />
              {t('check_now')}
            </Button>
          }
        >
          {check.error && <ErrorBox error={check.error} />}
          {watches.isPending ? (
            <Spinner />
          ) : !watches.data?.length ? (
            <Empty />
          ) : (
            <ul className="flex flex-col divide-y divide-[var(--border)]">
              {watches.data.map((w) => (
                <li key={w.id} className="flex items-start justify-between gap-2 py-2.5">
                  <div className="flex min-w-0 flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <ChainBadge chain={w.chain} />
                      {w.name && <b className="text-sm">{w.name}</b>}
                      {w.telegram_chat_id && (
                        <span className="inline-flex items-center gap-1 text-xs text-muted">
                          <Send className="size-3" />
                          {t('telegram')}
                        </span>
                      )}
                    </div>
                    <Address address={w.address} chain={w.chain} />
                    <span className="text-xs text-muted">
                      {Number(w.min_amount) > 0 && `≥ ${fmtAmount(w.min_amount, lang)} ${w.token ?? ''} · `}
                      {t('last_checked')}: {fmtDate(w.last_checked_at, lang)}
                    </span>
                  </div>
                  <button className="rounded p-1 text-muted hover:bg-critical/10 hover:text-critical" onClick={() => remove.mutate(w.id)} title={t('delete')}>
                    <Trash2 className="size-4" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title={t('alerts')}
          actions={
            <Button size="sm" variant="ghost" onClick={() => readAll.mutate()} loading={readAll.isPending}>
              <CheckCheck className="size-3.5" />
              {t('mark_all_read')}
            </Button>
          }
        >
          {alerts.isPending ? (
            <Spinner />
          ) : !alerts.data?.length ? (
            <Empty>{t('no_alerts')}</Empty>
          ) : (
            <ul className="flex flex-col gap-2">
              {alerts.data.map((a) => (
                <li
                  key={a.id}
                  className={clsx('flex flex-col gap-1 rounded-lg border p-3 text-sm', a.read ? 'border-line' : 'border-accent bg-accent-soft/40')}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 font-bold">
                      {a.direction === 'in' ? (
                        <ArrowDownLeft className="size-4 text-[var(--series-in)]" />
                      ) : (
                        <ArrowUpRight className="size-4 text-[var(--series-out)]" />
                      )}
                      {fmtAmount(a.amount, lang)} {a.token_symbol}
                      {a.watch_name && <span className="font-normal text-ink-2">· {a.watch_name}</span>}
                    </span>
                    <Worth amount={a.amount} symbol={a.token_symbol} chain={a.chain} at={a.timestamp} />
                    <span className="text-xs text-muted">{fmtDate(a.timestamp, lang)}</span>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-2 text-xs">
                    <span className="text-ink-2">{t('counterparty')}:</span>
                    <Address address={a.counterparty} chain={a.chain} />
                  </div>
                  <TxLink hash={a.tx_hash} chain={a.chain} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}
