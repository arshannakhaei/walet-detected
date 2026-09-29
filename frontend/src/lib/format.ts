import type { Lang } from './i18n'

export function shortAddr(a: string, head = 6, tail = 4): string {
  return a.length > head + tail + 1 ? `${a.slice(0, head)}…${a.slice(-tail)}` : a
}

/** Amounts arrive as decimal strings; keep precision sensible for display. */
export function fmtAmount(value: string | number | null | undefined, lang: Lang = 'en'): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return String(value)
  const abs = Math.abs(n)
  const digits = abs >= 1000 ? 0 : abs >= 1 ? 2 : abs === 0 ? 0 : 6
  return n.toLocaleString(lang === 'fa' ? 'fa-IR' : 'en-US', { maximumFractionDigits: digits })
}

export function fmtCompact(value: string | number, lang: Lang = 'en'): string {
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return String(value)
  return n.toLocaleString(lang === 'fa' ? 'fa-IR' : 'en-US', { notation: 'compact', maximumFractionDigits: 1 })
}

export function fmtUsd(value: string | number | null | undefined, lang: Lang = 'en'): string {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  return `$${n.toLocaleString(lang === 'fa' ? 'fa-IR' : 'en-US', { maximumFractionDigits: n >= 100 ? 0 : 2 })}`
}

export function fmtDate(iso: string | null | undefined, lang: Lang = 'en', withTime = true): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const locale = lang === 'fa' ? 'fa-IR' : 'en-GB'
  return withTime
    ? d.toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' })
    : d.toLocaleDateString(locale, { dateStyle: 'medium' })
}

export function fmtPercent(value: string | number, lang: Lang = 'en'): string {
  return Number(value).toLocaleString(lang === 'fa' ? 'fa-IR' : 'en-US', { style: 'percent', maximumFractionDigits: 0 })
}

export function fmtNumber(value: number, lang: Lang = 'en', digits = 0): string {
  return value.toLocaleString(lang === 'fa' ? 'fa-IR' : 'en-US', { maximumFractionDigits: digits })
}
