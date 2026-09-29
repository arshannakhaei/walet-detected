import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { RiskBadge, SeverityBadge } from '../../components/badges'
import { Card, ErrorBox, Spinner, Stat, Toggle } from '../../components/ui'
import { api, type Chain } from '../../lib/api'
import { fmtNumber, fmtPercent } from '../../lib/format'
import { FINDING_TITLES_FA } from '../../lib/findings'
import { useI18n } from '../../lib/i18n'

const LEVEL_COLOR = { low: 'var(--good)', medium: 'var(--warning)', high: 'var(--serious)', critical: 'var(--critical)' }

/** Evidence strings start with an address or tx hash; link addresses to their wallet page. */
function Evidence({ text, chain }: { text: string; chain: Chain }) {
  const first = text.split(/[\s:(]/)[0]
  // EVM address, or a base58 address (Tron, Bitcoin, Solana); tx hashes are longer.
  const isAddress = /^0x[0-9a-fA-F]{40}$/.test(first) || /^(bc1[0-9a-z]{11,71}|[1-9A-HJ-NP-Za-km-z]{26,44})$/.test(first)
  if (isAddress) {
    return (
      <span className="mono break-all text-xs">
        <Link to={`/wallet/${chain}/${first}`} className="text-accent hover:underline">
          {first}
        </Link>
        {text.slice(first.length)}
      </span>
    )
  }
  return <span className="mono break-all text-xs text-ink-2">{text}</span>
}

export function RiskTab({ chain, address }: { chain: Chain; address: string }) {
  const { t, lang } = useI18n()
  const [deep, setDeep] = useState(false)
  const q = useQuery({ queryKey: ['risk', chain, address, deep], queryFn: () => api.risk(address, chain, deep) })

  if (q.isPending) return <Spinner label={deep ? t('loading_chain') : t('loading')} />
  if (q.isError) return <ErrorBox error={q.error} onRetry={() => q.refetch()} />
  const r = q.data
  const s = r.stats

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          <div
            className="flex size-24 shrink-0 flex-col items-center justify-center rounded-full border-8"
            style={{ borderColor: LEVEL_COLOR[r.level] }}
          >
            <span className="text-2xl font-bold">{r.score}</span>
            <span className="text-[10px] text-muted">/ 100</span>
          </div>
          <div className="flex flex-1 flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-bold">{t('risk_score')}</span>
              <RiskBadge report={r} />
            </div>
            <p className="text-xs text-muted">{t('risk_disclaimer')}</p>
            <Toggle checked={deep} onChange={setDeep} label={t('deep_scan')} />
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label={t('median_holding')}
          value={s.median_holding_hours === null ? '—' : `${fmtNumber(s.median_holding_hours, lang, 1)} ${t('hours')}`}
        />
        <Stat
          label={`${t('pass_through')}${s.main_token ? ` (${s.main_token})` : ''}`}
          value={s.pass_through_ratio === null ? '—' : fmtPercent(s.pass_through_ratio, lang)}
        />
        <Stat
          label={t('lifetime')}
          value={s.lifetime_days === null ? '—' : `${fmtNumber(s.lifetime_days, lang, 0)} ${t('days')}`}
          sub={`${t('active_days')}: ${fmtNumber(s.active_days, lang)}`}
        />
        <Stat
          label={`${t('senders')} / ${t('receivers')}`}
          value={`${fmtNumber(s.distinct_senders, lang)} / ${fmtNumber(s.distinct_receivers, lang)}`}
        />
      </div>

      <Card title={t('evidence')}>
        {r.findings.length === 0 ? (
          <p className="text-sm text-ink-2">{t('no_findings')}</p>
        ) : (
          <ul className="flex flex-col gap-4">
            {r.findings.map((f) => (
              <li key={f.code} className="flex flex-col gap-1.5 border-b border-line pb-4 last:border-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={f.severity} />
                  <b>{(lang === 'fa' && FINDING_TITLES_FA[f.code]) || f.title}</b>
                  {f.points > 0 && <span className="text-xs text-muted">+{f.points}</span>}
                </div>
                <p dir="auto" className="text-sm text-ink-2">{f.detail}</p>
                {f.evidence.length > 0 && (
                  <div className="flex flex-col gap-0.5 rounded-lg bg-surface-2 p-2">
                    {f.evidence.slice(0, 10).map((e) => (
                      <Evidence key={e} text={e} chain={chain} />
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
