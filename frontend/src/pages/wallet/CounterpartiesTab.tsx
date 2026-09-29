import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { ArrowDownUp, Download, Tag } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Address } from '../../components/Address'
import { LabelDialog } from '../../components/dialogs'
import { FilterBar } from '../../components/FilterBar'
import { Card, Empty, ErrorBox, Input, Spinner, TableWrap, td, th } from '../../components/ui'
import { api, walletPath, type Chain, type Label, type TransferFilters, type WalletOverview } from '../../lib/api'
import { fmtAmount, fmtDate, fmtNumber } from '../../lib/format'
import { useI18n } from '../../lib/i18n'

type SortKey = 'total' | 'received' | 'sent' | 'count' | 'last'

export function CounterpartiesTab({
  chain,
  address,
  overview,
  labels,
}: {
  chain: Chain
  address: string
  overview: WalletOverview
  labels: Map<string, Label>
}) {
  const { t, lang } = useI18n()
  const [filters, setFilters] = useState<TransferFilters>({})
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortKey>('total')
  const [labelFor, setLabelFor] = useState<string | null>(null)
  const tokens = overview.flows.map((f) => f.token_symbol)

  const q = useQuery({
    queryKey: ['counterparties', chain, address, filters],
    queryFn: () => api.counterparties(address, chain, filters),
  })

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const list = (q.data ?? []).filter(
      (c) => !needle || c.address.toLowerCase().includes(needle) || labels.get(c.address)?.name.toLowerCase().includes(needle),
    )
    const key = (c: (typeof list)[number]) => {
      switch (sort) {
        case 'received':
          return Number(c.received_from)
        case 'sent':
          return Number(c.sent_to)
        case 'count':
          return c.count_in + c.count_out
        case 'last':
          return new Date(c.last_seen).getTime()
        default:
          return Number(c.received_from) + Number(c.sent_to)
      }
    }
    return [...list].sort((a, b) => key(b) - key(a))
  }, [q.data, search, sort, labels])

  const header = (key: SortKey, text: string) => (
    <button className={clsx('inline-flex items-center gap-1', sort === key && 'text-accent')} onClick={() => setSort(key)}>
      {text}
      <ArrowDownUp className="size-3" />
    </button>
  )

  return (
    <Card
      title={`${t('tab_counterparties')}${q.data ? ` (${fmtNumber(q.data.length, lang)})` : ''}`}
      actions={
        <a
          href={walletPath(address, 'counterparties.csv', chain, { ...filters })}
          className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
        >
          <Download className="size-3.5" />
          {t('export_csv')}
        </a>
      }
    >
      <div className="flex flex-col gap-3">
        <FilterBar value={filters} onChange={setFilters} tokens={tokens} />
        <Input placeholder={t('search_table')} value={search} onChange={(e) => setSearch(e.target.value)} className="sm:max-w-xs" />
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorBox error={q.error} onRetry={() => q.refetch()} />
        ) : rows.length === 0 ? (
          <Empty />
        ) : (
          <TableWrap>
            <thead>
              <tr>
                <th className={th}>{t('address')}</th>
                <th className={th}>{t('token')}</th>
                <th className={th}>{header('received', t('received_from'))}</th>
                <th className={th}>{header('sent', t('sent_to'))}</th>
                <th className={th}>{header('count', t('count'))}</th>
                <th className={th}>{header('last', t('last_seen'))}</th>
                <th className={th} />
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 500).map((c) => (
                <tr key={`${c.address}:${c.token_symbol}:${c.token_contract}`} className="hover:bg-surface-2">
                  <td className={td}>
                    <Address address={c.address} chain={chain} label={labels.get(c.address)} />
                  </td>
                  <td className={td}>{c.token_symbol}</td>
                  <td className={`${td} tabular`}>{Number(c.received_from) ? fmtAmount(c.received_from, lang) : '—'}</td>
                  <td className={`${td} tabular`}>{Number(c.sent_to) ? fmtAmount(c.sent_to, lang) : '—'}</td>
                  <td className={`${td} tabular text-ink-2`}>
                    ↓{fmtNumber(c.count_in, lang)} ↑{fmtNumber(c.count_out, lang)}
                  </td>
                  <td className={`${td} text-xs text-ink-2`}>{fmtDate(c.last_seen, lang)}</td>
                  <td className={td}>
                    <button className="rounded p-1 text-muted hover:bg-surface hover:text-ink" onClick={() => setLabelFor(c.address)} title={t('label_it')}>
                      <Tag className="size-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </div>
      {labelFor && (
        <LabelDialog open onClose={() => setLabelFor(null)} chain={chain} address={labelFor} current={labels.get(labelFor)} />
      )}
    </Card>
  )
}
