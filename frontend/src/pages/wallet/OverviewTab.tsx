import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowDownLeft, ArrowUpRight } from 'lucide-react'
import { useEffect, useState } from 'react'
import { RiskBadge, SeverityBadge } from '../../components/badges'
import { TimelineChart } from '../../components/TimelineChart'
import { Badge, Button, Card, Empty, Segmented, Select, Spinner, Stat, TableWrap, td, th } from '../../components/ui'
import { api, type RiskReport, type WalletOverview } from '../../lib/api'
import { fmtAmount, fmtDate, fmtNumber, fmtUsd } from '../../lib/format'
import { FINDING_TITLES_FA } from '../../lib/findings'
import { useI18n } from '../../lib/i18n'

export function OverviewTab({
  overview: o,
  risk,
  onTab,
}: {
  overview: WalletOverview
  risk?: RiskReport
  onTab: (tab: 'risk') => void
}) {
  const { t, lang } = useI18n()
  const [bucket, setBucket] = useState<'day' | 'week' | 'month'>('day')
  const [asTable, setAsTable] = useState(false)
  const tokens = o.flows.map((f) => f.token_symbol)
  const [token, setToken] = useState(tokens[0] ?? '')
  useEffect(() => setToken(tokens[0] ?? ''), [o.address]) // eslint-disable-line react-hooks/exhaustive-deps

  const timeline = useQuery({
    queryKey: ['timeline', o.chain, o.address, bucket, token],
    queryFn: () => api.timeline(o.address, o.chain, bucket, token || undefined),
    enabled: !!token,
  })

  return (
    <div className="flex flex-col gap-4">
      {o.truncated && (
        <div className="flex items-center gap-2 rounded-lg border border-warning/50 bg-warning/10 p-3 text-sm">
          <AlertTriangle className="size-4 shrink-0" />
          {t('truncated')}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label={t('total_value')} value={o.total_usd !== null ? fmtUsd(o.total_usd, lang) : '—'} />
        <Stat label={t('transfers_count')} value={fmtNumber(o.transfer_count, lang)} />
        <Stat label={t('counterparties_count')} value={fmtNumber(o.counterparty_count, lang)} />
        <Stat
          label={t('first_seen')}
          value={<span className="text-base">{fmtDate(o.first_seen, lang, false)}</span>}
          sub={`${t('last_seen')}: ${fmtDate(o.last_seen, lang, false)}`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title={t('balances')} className="lg:col-span-1">
          {o.balances.length === 0 ? (
            <Empty />
          ) : (
            <ul className="flex flex-col divide-y divide-[var(--border)]">
              {o.balances.map((b) => (
                <li key={`${b.token_symbol}:${b.token_contract}`} className="flex items-center justify-between gap-2 py-2">
                  <span className="flex items-center gap-2 font-medium">
                    {b.token_symbol}
                    {b.derived && <Badge title={t('derived')}>≈</Badge>}
                  </span>
                  <span className="text-end">
                    <div className="tabular font-bold">{fmtAmount(b.amount, lang)}</div>
                    {b.usd_value !== null && <div className="text-xs text-muted">{fmtUsd(b.usd_value, lang)}</div>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title={t('tab_risk')}
          className="lg:col-span-2"
          actions={
            <Button size="sm" variant="ghost" onClick={() => onTab('risk')}>
              {t('evidence')}
            </Button>
          }
        >
          {!risk ? (
            <Spinner />
          ) : (
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-3xl font-bold">{risk.score}</span>
                <RiskBadge report={risk} />
              </div>
              {risk.findings.length === 0 ? (
                <p className="text-sm text-ink-2">{t('no_findings')}</p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {risk.findings.slice(0, 4).map((f) => (
                    <li key={f.code} className="flex items-start gap-2 text-sm">
                      <SeverityBadge severity={f.severity} />
                      <span>
                        <b>{(lang === 'fa' && FINDING_TITLES_FA[f.code]) || f.title}</b> — <span dir="auto" className="text-ink-2">{f.detail}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </Card>
      </div>

      <Card
        title={t('activity')}
        actions={
          <>
            <Select value={token} onChange={(e) => setToken(e.target.value)} className="h-8 w-28 text-xs">
              {tokens.map((tk) => (
                <option key={tk}>{tk}</option>
              ))}
            </Select>
            <Segmented
              value={bucket}
              onChange={setBucket}
              options={[
                { id: 'day', label: t('day') },
                { id: 'week', label: t('week') },
                { id: 'month', label: t('month') },
              ]}
            />
            <Segmented
              value={asTable ? 'table' : 'chart'}
              onChange={(v) => setAsTable(v === 'table')}
              options={[
                { id: 'chart', label: t('chart_view') },
                { id: 'table', label: t('table_view') },
              ]}
            />
          </>
        }
      >
        {!token ? <Empty /> : timeline.isPending ? <Spinner /> : <TimelineChart rows={timeline.data ?? []} asTable={asTable} />}
      </Card>

      <Card title={t('flows')}>
        {o.flows.length === 0 ? (
          <Empty />
        ) : (
          <TableWrap>
            <thead>
              <tr>
                <th className={th}>{t('token')}</th>
                <th className={th}>
                  <ArrowDownLeft className="inline size-3.5 text-[var(--series-in)]" /> {t('inflow')}
                </th>
                <th className={th}>
                  <ArrowUpRight className="inline size-3.5 text-[var(--series-out)]" /> {t('outflow')}
                </th>
              </tr>
            </thead>
            <tbody>
              {o.flows.map((f) => (
                <tr key={`${f.token_symbol}:${f.token_contract}`}>
                  <td className={`${td} font-medium`}>{f.token_symbol}</td>
                  <td className={`${td} tabular`}>
                    {fmtAmount(f.total_in, lang)} <span className="text-muted">({fmtNumber(f.count_in, lang)})</span>
                    {f.usd_in !== null && <div className="text-xs text-muted">{fmtUsd(f.usd_in, lang)}</div>}
                  </td>
                  <td className={`${td} tabular`}>
                    {fmtAmount(f.total_out, lang)} <span className="text-muted">({fmtNumber(f.count_out, lang)})</span>
                    {f.usd_out !== null && <div className="text-xs text-muted">{fmtUsd(f.usd_out, lang)}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Card>
    </div>
  )
}
