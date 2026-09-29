import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ArrowDownLeft, ArrowUpRight, Download, Repeat, Route } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Address, TxLink } from '../../components/Address'
import { FilterBar } from '../../components/FilterBar'
import { Badge, Button, Card, Empty, ErrorBox, Spinner, TableWrap, td, th } from '../../components/ui'
import { api, walletPath, type Chain, type Label, type TransferFilters, type WalletOverview } from '../../lib/api'
import { fmtAmount, fmtDate, fmtNumber } from '../../lib/format'
import { useI18n } from '../../lib/i18n'

const PAGE = 50

export function TransfersTab({
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
  const [page, setPage] = useState(0)
  const tokens = overview.flows.map((f) => f.token_symbol)

  const q = useQuery({
    queryKey: ['transfers', chain, address, filters, page],
    queryFn: () => api.transfers(address, chain, filters, PAGE, page * PAGE),
    placeholderData: keepPreviousData,
  })
  const total = q.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE))

  return (
    <Card
      title={`${t('tab_transfers')}${q.data ? ` (${fmtNumber(total, lang)})` : ''}`}
      actions={
        <a href={walletPath(address, 'transfers.csv', chain, { ...filters })} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
          <Download className="size-3.5" />
          {t('export_csv')}
        </a>
      }
    >
      <div className="flex flex-col gap-3">
        <FilterBar
          value={filters}
          onChange={(f) => {
            setFilters(f)
            setPage(0)
          }}
          tokens={tokens}
        />
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorBox error={q.error} onRetry={() => q.refetch()} />
        ) : q.data.items.length === 0 ? (
          <Empty />
        ) : (
          <>
            <TableWrap>
              <thead>
                <tr>
                  <th className={th}>{t('time')}</th>
                  <th className={th}>{t('direction')}</th>
                  <th className={th}>{t('counterparty')}</th>
                  <th className={th}>{t('amount')}</th>
                  <th className={th}>{t('tx')}</th>
                  <th className={th} />
                </tr>
              </thead>
              <tbody>
                {q.data.items.map(({ transfer: tr, direction, counterparty }) => (
                  <tr key={tr.transfer_id} className="hover:bg-surface-2">
                    <td className={`${td} whitespace-nowrap text-xs text-ink-2`}>{fmtDate(tr.timestamp, lang)}</td>
                    <td className={td}>
                      {direction === 'in' ? (
                        <Badge tone="accent">
                          <ArrowDownLeft className="size-3" />
                          {t('dir_in')}
                        </Badge>
                      ) : direction === 'out' ? (
                        <Badge tone="serious">
                          <ArrowUpRight className="size-3" />
                          {t('dir_out')}
                        </Badge>
                      ) : (
                        <Badge>
                          <Repeat className="size-3" />
                        </Badge>
                      )}
                    </td>
                    <td className={td}>
                      <Address address={counterparty} chain={chain} label={labels.get(counterparty)} />
                    </td>
                    <td className={`${td} whitespace-nowrap`}>
                      <span className="tabular font-bold">{fmtAmount(tr.amount, lang)}</span> <span className="text-ink-2">{tr.token_symbol}</span>
                      {!tr.success && (
                        <Badge tone="critical" className="ms-1">
                          {t('failed')}
                        </Badge>
                      )}
                    </td>
                    <td className={td}>
                      <TxLink hash={tr.tx_hash} chain={chain} />
                    </td>
                    <td className={td}>
                      <Link
                        to={`/trace?chain=${chain}&address=${encodeURIComponent(address)}&tx=${encodeURIComponent(tr.tx_hash)}&token=${encodeURIComponent(tr.token_contract ?? tr.token_symbol)}&direction=forward`}
                        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-accent hover:bg-accent-soft"
                      >
                        <Route className="size-3.5" />
                        {t('trace_this')}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
            <div className="flex items-center justify-between gap-2 text-sm">
              <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                {t('prev')}
              </Button>
              <span className="tabular text-ink-2">
                {fmtNumber(page + 1, lang)} {t('of')} {fmtNumber(pages, lang)}
              </span>
              <Button size="sm" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>
                {t('next')}
              </Button>
            </div>
          </>
        )}
      </div>
    </Card>
  )
}
