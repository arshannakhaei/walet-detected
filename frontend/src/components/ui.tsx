import clsx from 'clsx'
import { AlertTriangle, Gauge, Inbox, KeyRound, Loader2, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { isRateLimit } from '../lib/api'
import {
  useEffect,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from 'react'
import { useI18n } from '../lib/i18n'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

export function Button({
  variant = 'secondary',
  size = 'md',
  className,
  loading,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: 'sm' | 'md'; loading?: boolean }) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={clsx(
        'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap',
        size === 'sm' ? 'h-8 px-2.5 text-xs' : 'h-10 px-4 text-sm',
        variant === 'primary' && 'bg-accent text-accent-ink hover:brightness-110',
        variant === 'secondary' && 'border border-line bg-surface text-ink hover:bg-surface-2',
        variant === 'ghost' && 'text-ink-2 hover:bg-surface-2 hover:text-ink',
        variant === 'danger' && 'border border-line bg-surface text-critical hover:bg-critical/10',
        className,
      )}
    >
      {loading && <Loader2 className="size-4 animate-spin" />}
      {children}
    </button>
  )
}

export function Card({ className, children, title, actions }: { className?: string; children: ReactNode; title?: ReactNode; actions?: ReactNode }) {
  return (
    <section className={clsx('rounded-xl border border-line bg-surface', className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
          <h2 className="text-sm font-bold text-ink">{title}</h2>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...rest}
      className={clsx(
        'h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none',
        className,
      )}
    />
  )
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...rest}
      className={clsx(
        'h-10 w-full rounded-lg border border-line bg-surface px-2 text-sm text-ink focus:border-accent focus:outline-none',
        className,
      )}
    >
      {children}
    </select>
  )
}

export function Textarea({ className, ...rest }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...rest}
      className={clsx(
        'w-full rounded-lg border border-line bg-surface p-3 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none',
        className,
      )}
    />
  )
}

export function Field({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={clsx('flex flex-col gap-1', className)}>
      <span className="text-xs font-medium text-ink-2">{label}</span>
      {children}
    </label>
  )
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode }) {
  return (
    <label className="inline-flex cursor-pointer select-none items-center gap-2 text-sm text-ink-2">
      <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  )
}

export function Badge({ children, tone = 'neutral', className, title }: { children: ReactNode; tone?: 'neutral' | 'accent' | 'good' | 'warning' | 'serious' | 'critical' | 'violet'; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={clsx(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap',
        tone === 'neutral' && 'bg-surface-2 text-ink-2',
        tone === 'accent' && 'bg-accent-soft text-accent',
        tone === 'good' && 'bg-good/15 text-good',
        tone === 'warning' && 'bg-warning/20 text-ink',
        tone === 'serious' && 'bg-serious/20 text-ink',
        tone === 'critical' && 'bg-critical/15 text-critical',
        tone === 'violet' && 'bg-[var(--violet)]/15 text-[var(--violet)]',
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Spinner({ label }: { label?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-sm text-ink-2" role="status">
      <Loader2 className="size-7 animate-spin text-accent" />
      {label}
    </div>
  )
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useI18n()
  const message = error instanceof Error ? error.message : String(error)
  if (isRateLimit(error)) {
    return (
      <div className="flex flex-col gap-3 rounded-xl border border-warning/50 bg-warning/10 p-4 text-sm">
        <div className="flex items-start gap-3">
          <Gauge className="mt-0.5 size-5 shrink-0" />
          <div className="flex-1">
            <b>{t('rate_limited_title')}</b>
            <p className="mt-1 text-ink-2">{t('rate_limited_body')}</p>
            <p className="mono mt-1 text-xs text-muted">{message}</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/settings">
            <Button size="sm" variant="primary" tabIndex={-1}>
              <KeyRound className="size-3.5" />
              {t('add_key_now')}
            </Button>
          </Link>
          {onRetry && (
            <Button size="sm" onClick={onRetry}>
              {t('retry')}
            </Button>
          )}
        </div>
      </div>
    )
  }
  return (
    <div className="flex flex-col items-start gap-3 rounded-xl border border-critical/40 bg-critical/5 p-4 text-sm sm:flex-row sm:items-center">
      <AlertTriangle className="size-5 shrink-0 text-critical" />
      <div className="flex-1 break-words">
        <b>{t('error')}:</b> {message}
      </div>
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          {t('retry')}
        </Button>
      )}
    </div>
  )
}

export function Empty({ children }: { children?: ReactNode }) {
  const { t } = useI18n()
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-sm text-muted">
      <Inbox className="size-6" />
      {children ?? t('empty')}
    </div>
  )
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode; icon?: ReactNode }[]; value: T; onChange: (id: T) => void }) {
  return (
    <div className="scroll-thin -mx-1 flex gap-1 overflow-x-auto px-1" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={value === tab.id}
          onClick={() => onChange(tab.id)}
          className={clsx(
            'inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
            value === tab.id ? 'bg-accent text-accent-ink' : 'text-ink-2 hover:bg-surface-2',
          )}
        >
          {tab.icon}
          {tab.label}
        </button>
      ))}
    </div>
  )
}

export function Segmented<T extends string>({ options, value, onChange }: { options: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.id}
          onClick={() => onChange(o.id)}
          className={clsx(
            'rounded-md px-2.5 py-1 text-xs font-medium',
            value === o.id ? 'bg-surface text-ink shadow-sm' : 'text-ink-2 hover:text-ink',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className="max-h-[90vh] w-full overflow-y-auto rounded-t-2xl border border-line bg-surface p-5 shadow-xl sm:max-w-lg sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between gap-4">
          <h3 className="text-base font-bold">{title}</h3>
          <button onClick={onClose} className="rounded-md p-1 text-ink-2 hover:bg-surface-2" aria-label="close">
            <X className="size-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

export function Stat({ label, value, sub }: { label: ReactNode; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="text-xs text-ink-2">{label}</div>
      <div className="mt-1 text-xl font-bold text-ink">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  )
}

/** Horizontally scrollable table wrapper, so wide tables never widen the page. */
export function TableWrap({ children }: { children: ReactNode }) {
  return (
    <div className="scroll-thin -mx-4 overflow-x-auto px-4">
      <table className="w-full min-w-[640px] border-collapse text-sm">{children}</table>
    </div>
  )
}

export const th = 'border-b border-line px-2 py-2 text-start text-xs font-medium text-ink-2 whitespace-nowrap'
export const td = 'border-b border-line px-2 py-2 align-top'
