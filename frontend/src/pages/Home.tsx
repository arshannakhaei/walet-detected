import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { Activity, Eye, FlaskConical, GitFork, Route, Search, ShieldAlert, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ChainBadge } from '../components/badges'
import { Button } from '../components/ui'
import { api, type Chain } from '../lib/api'
import { CHAINS } from '../lib/chains'
import { fmtDate, shortAddr } from '../lib/format'
import { useI18n } from '../lib/i18n'
import { clearRecent, recentSearches } from '../lib/search'

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return v
}

export function Home() {
  const { t, lang } = useI18n()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [value, setValue] = useState(params.get('q') ?? '')
  const [chosen, setChosen] = useState<Chain | null>(null)
  const [recent, setRecent] = useState(recentSearches)
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, staleTime: Infinity })
  const demo = health.data?.demo
  const typed = useDebounced(value.trim(), 250)

  const detect = useQuery({
    queryKey: ['detect', typed],
    queryFn: () => api.detect(typed),
    enabled: typed.length >= 20,
  })
  const candidates = detect.data?.chains ?? []
  const multi = candidates.length > 1
  const activity = useQuery({
    queryKey: ['activity', typed],
    queryFn: () => api.activity(typed),
    enabled: multi,
    staleTime: 5 * 60_000,
  })
  const active = new Map((activity.data ?? []).map((a) => [a.chain, a.active]))

  useEffect(() => setChosen(null), [typed])
  useEffect(() => {
    // Preselect the first chain the address has actually been used on.
    if (multi && !chosen && activity.data) {
      const first = activity.data.find((a) => a.active)
      if (first) setChosen(first.chain)
    }
  }, [multi, chosen, activity.data])

  const target = chosen ?? candidates.find((c) => c.supported)?.chain ?? null

  const go = (chain: Chain | null) => {
    if (chain && typed) navigate(`/wallet/${chain}/${typed}`)
  }

  const features = [
    { icon: GitFork, title: t('feature_graph'), text: t('feature_graph_d') },
    { icon: Route, title: t('feature_trace'), text: t('feature_trace_d'), to: '/trace' },
    { icon: ShieldAlert, title: t('feature_risk'), text: t('feature_risk_d') },
    { icon: Eye, title: t('feature_watch'), text: t('feature_watch_d'), to: '/watchlist' },
  ]

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-8 pt-4 sm:pt-10">
      <div className="text-center">
        <h1 className="text-2xl font-bold sm:text-3xl">{t('tagline')}</h1>
        <p className="mt-2 text-sm text-ink-2">{t('search_hint')}</p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          go(target)
        }}
        className="flex flex-col gap-3"
      >
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute top-1/2 size-5 -translate-y-1/2 text-muted start-3" />
            <input
              autoFocus
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={t('search_placeholder')}
              spellCheck={false}
              className="mono h-12 w-full rounded-xl border border-line bg-surface text-sm text-ink shadow-sm placeholder:font-sans placeholder:text-muted focus:border-accent focus:outline-none ps-11 pe-3"
            />
          </div>
          <Button type="submit" variant="primary" className="h-12 px-6" disabled={!target}>
            {t('search_button')}
          </Button>
        </div>

        {typed.length >= 20 && detect.data && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {candidates.length === 0 ? (
              <span className="text-critical">{t('unrecognized')}</span>
            ) : (
              <>
                <span className="text-ink-2">{t('detected')}:</span>
                {candidates.map(({ chain, supported }) => (
                  <button
                    type="button"
                    key={chain}
                    disabled={!supported}
                    onClick={() => (multi ? setChosen(chain) : go(chain))}
                    className={clsx(
                      'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium disabled:opacity-40',
                      target === chain ? 'border-accent bg-accent-soft text-accent' : 'border-line bg-surface',
                    )}
                  >
                    <span className="size-2 rounded-full" style={{ background: CHAINS[chain].color }} />
                    {CHAINS[chain].name}
                    {!supported && <span>({t('not_enabled')})</span>}
                    {active.get(chain) && <Activity className="size-3 text-good" />}
                  </button>
                ))}
              </>
            )}
          </div>
        )}
      </form>

      {demo && (
        <section className="flex flex-col gap-2 rounded-xl border border-dashed border-accent bg-accent-soft/40 p-4">
          <div className="flex items-center gap-2 text-sm font-bold text-accent">
            <FlaskConical className="size-4" />
            {t('demo_title')}
          </div>
          <div className="flex flex-wrap gap-2">
            {(
              [
                ['demo_scammer', demo.scammer],
                ['demo_victim', demo.victim],
                ['demo_mule', demo.mule],
              ] as const
            ).map(([key, address]) => (
              <Link key={key} to={`/wallet/tron/${address}`}>
                <Button size="sm" tabIndex={-1}>
                  {t(key)}
                </Button>
              </Link>
            ))}
          </div>
        </section>
      )}

      {recent.length > 0 && (
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-bold text-ink-2">{t('recent')}</h2>
            <button
              className="flex items-center gap-1 text-xs text-muted hover:text-ink"
              onClick={() => {
                clearRecent()
                setRecent([])
              }}
            >
              <X className="size-3" />
              {t('clear')}
            </button>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {recent.map((r) => (
              <Link
                key={`${r.chain}:${r.address}`}
                to={`/wallet/${r.chain}/${r.address}`}
                className="flex items-center justify-between gap-2 rounded-lg border border-line bg-surface px-3 py-2 hover:border-accent"
              >
                <span className="mono truncate text-sm">{shortAddr(r.address, 10, 8)}</span>
                <span className="flex shrink-0 items-center gap-2">
                  <span className="hidden text-xs text-muted sm:inline">{fmtDate(new Date(r.at).toISOString(), lang, false)}</span>
                  <ChainBadge chain={r.chain as Chain} />
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <section className="grid gap-3 sm:grid-cols-2">
        {features.map(({ icon: Icon, title, text, to }) => {
          const body = (
            <>
              <Icon className="size-5 text-accent" />
              <div className="mt-2 font-bold">{title}</div>
              <div className="mt-1 text-sm text-ink-2">{text}</div>
            </>
          )
          return to ? (
            <Link key={title} to={to} className="rounded-xl border border-line bg-surface p-4 hover:border-accent">
              {body}
            </Link>
          ) : (
            <div key={title} className="rounded-xl border border-line bg-surface p-4">
              {body}
            </div>
          )
        })}
      </section>
    </div>
  )
}
