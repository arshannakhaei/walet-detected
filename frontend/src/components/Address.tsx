import clsx from 'clsx'
import { Check, Copy, ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { Chain, Label } from '../lib/api'
import { CHAINS } from '../lib/chains'
import { shortAddr } from '../lib/format'
import { useI18n } from '../lib/i18n'
import { LabelBadge } from './badges'

export function CopyButton({ text, className }: { text: string; className?: string }) {
  const { t } = useI18n()
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      title={done ? t('copied') : t('copy')}
      aria-label={t('copy')}
      className={clsx('rounded p-1 text-muted hover:bg-surface-2 hover:text-ink', className)}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        navigator.clipboard?.writeText(text).then(() => {
          setDone(true)
          setTimeout(() => setDone(false), 1200)
        })
      }}
    >
      {done ? <Check className="size-3.5 text-good" /> : <Copy className="size-3.5" />}
    </button>
  )
}

/** An address with copy + explorer actions; links to its wallet page. */
export function Address({
  address,
  chain,
  label,
  full = false,
  link = true,
}: {
  address: string
  chain: Chain
  label?: Label | null
  full?: boolean
  link?: boolean
}) {
  const { t } = useI18n()
  const text = full ? address : shortAddr(address)
  return (
    <span className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-x-1 gap-y-0.5">
      {link ? (
        <Link to={`/wallet/${chain}/${address}`} className="mono break-all text-accent hover:underline" title={address}>
          {text}
        </Link>
      ) : (
        <span className="mono break-all" title={address}>
          {text}
        </span>
      )}
      <CopyButton text={address} />
      <a
        href={CHAINS[chain].address(address)}
        target="_blank"
        rel="noreferrer"
        title={t('open_explorer')}
        className="rounded p-1 text-muted hover:bg-surface-2 hover:text-ink"
      >
        <ExternalLink className="size-3.5" />
      </a>
      {label && <LabelBadge label={label} />}
    </span>
  )
}

export function TxLink({ hash, chain }: { hash: string; chain: Chain }) {
  return (
    <span className="inline-flex items-center gap-0.5">
      <a href={CHAINS[chain].tx(hash)} target="_blank" rel="noreferrer" className="mono text-accent hover:underline" title={hash}>
        {shortAddr(hash, 8, 6)}
      </a>
      <CopyButton text={hash} />
    </span>
  )
}
