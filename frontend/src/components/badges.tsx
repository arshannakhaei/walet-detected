import { ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react'
import type { Chain, Label, LabelCategory, RiskReport, Severity } from '../lib/api'
import { CHAINS } from '../lib/chains'
import { useI18n, type TKey } from '../lib/i18n'
import { Badge } from './ui'

export function ChainBadge({ chain }: { chain: Chain }) {
  const meta = CHAINS[chain]
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2 py-0.5 text-xs font-medium">
      <span className="size-2 rounded-full" style={{ background: meta.color }} />
      {meta.name}
    </span>
  )
}

const RISKY: LabelCategory[] = ['mixer', 'sanctioned', 'scam']
const TERMINAL: LabelCategory[] = ['exchange', 'bridge', 'defi', 'service', 'token_contract']

export function categoryTone(c: LabelCategory) {
  if (RISKY.includes(c)) return 'critical' as const
  if (TERMINAL.includes(c)) return 'good' as const
  return 'accent' as const
}

export function LabelBadge({ label }: { label: Label }) {
  const { t } = useI18n()
  return (
    <Badge tone={categoryTone(label.category)} className="max-w-full">
      <span className="truncate">{label.name}</span>
      <span className="opacity-70">· {t(`cat_${label.category}` as TKey)}</span>
    </Badge>
  )
}

const LEVEL_TONE = { low: 'good', medium: 'warning', high: 'serious', critical: 'critical' } as const

export function RiskBadge({ report }: { report: Pick<RiskReport, 'score' | 'level'> }) {
  const { t } = useI18n()
  const Icon = report.level === 'low' ? ShieldCheck : report.level === 'medium' ? ShieldQuestion : ShieldAlert
  return (
    <Badge tone={LEVEL_TONE[report.level]}>
      <Icon className="size-3.5" />
      {report.score}/100 · {t(`level_${report.level}` as TKey)}
    </Badge>
  )
}

const SEVERITY_TONE = { info: 'neutral', low: 'good', medium: 'warning', high: 'serious', critical: 'critical' } as const

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge tone={SEVERITY_TONE[severity]}>{severity}</Badge>
}
