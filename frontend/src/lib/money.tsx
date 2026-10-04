// Dollar and toman values next to token amounts.
//
// Every <Money> asks for the price of its (chain, token, day); requests made in the
// same moment are sent to the server as one batch and cached for the session.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react'
import { api, type Chain, type Quote } from './api'
import { fmtAmount } from './format'
import { useI18n, type Lang } from './i18n'
import { readJson, writeJson } from './storage'

export type CurrencyMode = 'token' | 'usd' | 'toman' | 'all'

interface QuoteRequest {
  chain: Chain
  symbol: string
  contract: string | null
  day: string | null // YYYY-MM-DD (UTC) of the transfer; null = today only
}

const keyOf = (r: QuoteRequest) => `${r.chain}|${r.symbol}|${r.contract ?? ''}|${r.day ?? ''}`

class QuoteStore {
  private cache = new Map<string, Quote | null>()
  private queued = new Map<string, QuoteRequest>()
  private listeners = new Set<() => void>()
  private timer: ReturnType<typeof setTimeout> | null = null

  subscribe = (fn: () => void) => {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  get = (key: string) => this.cache.get(key)

  request(r: QuoteRequest) {
    const key = keyOf(r)
    if (this.cache.has(key) || this.queued.has(key)) return
    this.queued.set(key, r)
    if (!this.timer) this.timer = setTimeout(() => void this.flush(), 40)
  }

  /** Forget everything, e.g. after the dollar rate was changed by hand. */
  reset() {
    this.cache.clear()
    this.listeners.forEach((fn) => fn())
  }

  private async flush() {
    this.timer = null
    const batch = [...this.queued.entries()].slice(0, 500)
    for (const [key] of batch) this.queued.delete(key)
    if (this.queued.size && !this.timer) this.timer = setTimeout(() => void this.flush(), 40)
    try {
      const quotes = await api.quotes(batch.map(([, r]) => r))
      batch.forEach(([key], i) => this.cache.set(key, quotes[i] ?? null))
    } catch {
      batch.forEach(([key]) => this.cache.set(key, null)) // shown as token amount only
    }
    this.listeners.forEach((fn) => fn())
  }
}

export const quoteStore = new QuoteStore()

interface MoneyCtx {
  mode: CurrencyMode
  setMode: (m: CurrencyMode) => void
}

const Ctx = createContext<MoneyCtx>({ mode: 'all', setMode: () => {} })

export function CurrencyProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<CurrencyMode>(() => readJson<CurrencyMode>('ct-currency', 'all'))
  const setMode = useCallback((m: CurrencyMode) => {
    setModeState(m)
    writeJson('ct-currency', m)
  }, [])
  const value = useMemo(() => ({ mode, setMode }), [mode, setMode])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useCurrency = () => useContext(Ctx)

export function useQuote(chain: Chain, symbol: string, contract: string | null | undefined, at?: string | null) {
  const { mode } = useCurrency()
  const req: QuoteRequest = { chain, symbol, contract: contract ?? null, day: at ? at.slice(0, 10) : null }
  const key = keyOf(req)
  const quote = useSyncExternalStore(quoteStore.subscribe, () => quoteStore.get(key))
  useEffect(() => {
    if (mode !== 'token') quoteStore.request(req)
  }, [key, mode]) // eslint-disable-line react-hooks/exhaustive-deps
  return quote
}

export function fmtToman(value: number, lang: Lang, compact = true): string {
  const locale = lang === 'fa' ? 'fa-IR' : 'en-US'
  const unit = lang === 'fa' ? 'تومان' : 'T'
  const abs = Math.abs(value)
  if (compact && abs >= 1e9) return `${(value / 1e9).toLocaleString(locale, { maximumFractionDigits: 2 })} ${lang === 'fa' ? 'میلیارد' : 'B'} ${unit}`
  if (compact && abs >= 1e6) return `${(value / 1e6).toLocaleString(locale, { maximumFractionDigits: 1 })} ${lang === 'fa' ? 'میلیون' : 'M'} ${unit}`
  return `${value.toLocaleString(locale, { maximumFractionDigits: 0 })} ${unit}`
}

export function fmtDollar(value: number, lang: Lang): string {
  const abs = Math.abs(value)
  return `$${value.toLocaleString(lang === 'fa' ? 'fa-IR' : 'en-US', { maximumFractionDigits: abs >= 100 ? 0 : 2 })}`
}

export interface Worth {
  usdThen: number | null
  usdNow: number | null
  tomanThen: number | null
  tomanNow: number | null
}

export function worthOf(amount: number, q: Quote | null | undefined): Worth | null {
  if (!q) return null
  const num = (v: string | null) => (v === null ? null : Number(v))
  const usdThen = q.usd_then !== null ? amount * Number(q.usd_then) : null
  const usdNow = q.usd_now !== null ? amount * Number(q.usd_now) : null
  const rThen = num(q.toman_rate_then)
  const rNow = num(q.toman_rate_now)
  return {
    usdThen,
    usdNow,
    tomanThen: usdThen !== null && rThen ? usdThen * rThen : null,
    tomanNow: usdNow !== null && rNow ? usdNow * rNow : null,
  }
}

/** "$3,999 · ۲۴۰ میلیون تومان" in the chosen mode; "" when nothing is known. */
export function worthText(usd: number | null, toman: number | null, mode: CurrencyMode, lang: Lang): string {
  const parts: string[] = []
  if ((mode === 'usd' || mode === 'all') && usd !== null) parts.push(fmtDollar(usd, lang))
  if ((mode === 'toman' || mode === 'all') && toman !== null) parts.push(fmtToman(toman, lang))
  return parts.join(' · ')
}

function useWorth(amount: string | number, chain: Chain, symbol: string, contract: string | null | undefined, at?: string | null) {
  const { mode } = useCurrency()
  const { lang, t } = useI18n()
  const quote = useQuote(chain, symbol, contract, at)
  const w = mode === 'token' ? null : worthOf(Number(amount), quote)
  if (!w) return { line: '', title: undefined as string | undefined }
  const main = at ? worthText(w.usdThen ?? w.usdNow, w.tomanThen ?? w.tomanNow, mode, lang) : worthText(w.usdNow, w.tomanNow, mode, lang)
  const then = worthText(w.usdThen, w.tomanThen, 'all', lang)
  const now = worthText(w.usdNow, w.tomanNow, 'all', lang)
  const title = [at && then ? `${t('value_then')} (${at.slice(0, 10)}): ${then}` : '', now ? `${t('value_now')}: ${now}` : '']
    .filter(Boolean)
    .join('\n')
  return { line: main, title: title || undefined }
}

/** Token amount with its dollar / toman worth underneath (at the transfer time when `at` is given). */
export function Money({
  amount,
  symbol,
  contract,
  chain,
  at,
  showSymbol = true,
  className,
  strong = false,
}: {
  amount: string | number
  symbol: string
  contract?: string | null
  chain: Chain
  at?: string | null
  showSymbol?: boolean
  className?: string
  strong?: boolean
}) {
  const { lang } = useI18n()
  const { line, title } = useWorth(amount, chain, symbol, contract, at)
  return (
    <span className={`inline-flex flex-col leading-tight ${className ?? ''}`} title={title}>
      <span className={`tabular ${strong ? 'font-bold' : ''}`}>
        {fmtAmount(amount, lang)}
        {showSymbol && <span className="ms-1 font-normal text-ink-2">{symbol}</span>}
      </span>
      {line && <span className="mt-0.5 text-xs font-normal text-muted tabular">≈ {line}</span>}
    </span>
  )
}

/** Only the "≈ $… · … تومان" line, for places that format the token amount themselves. */
export function Worth({
  amount,
  symbol,
  contract,
  chain,
  at,
  className,
}: {
  amount: string | number
  symbol: string
  contract?: string | null
  chain: Chain
  at?: string | null
  className?: string
}) {
  const { line, title } = useWorth(amount, chain, symbol, contract, at)
  if (!line) return null
  return (
    <span className={`text-xs text-muted tabular ${className ?? ''}`} title={title}>
      ≈ {line}
    </span>
  )
}
