import { SlidersHorizontal } from 'lucide-react'
import { useState } from 'react'
import type { TransferFilters } from '../lib/api'
import { useI18n } from '../lib/i18n'
import { Button, Field, Input, Select, Toggle } from './ui'

/** Filter row shared by the transfers, counterparties and graph tabs. */
export function FilterBar({
  value,
  onChange,
  tokens,
  showDirection = true,
}: {
  value: TransferFilters
  onChange: (f: TransferFilters) => void
  tokens: string[]
  showDirection?: boolean
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const set = (patch: Partial<TransferFilters>) => onChange({ ...value, ...patch })
  const active = [value.token, value.direction, value.min_amount, value.max_amount, value.start, value.end].filter(Boolean).length

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-2">
        <Field label={t('token')} className="w-36">
          <Select value={value.token ?? ''} onChange={(e) => set({ token: e.target.value || undefined })}>
            <option value="">{t('all')}</option>
            {tokens.map((tk) => (
              <option key={tk} value={tk}>
                {tk}
              </option>
            ))}
          </Select>
        </Field>
        {showDirection && (
          <Field label={t('direction')} className="w-32">
            <Select value={value.direction ?? ''} onChange={(e) => set({ direction: (e.target.value || undefined) as TransferFilters['direction'] })}>
              <option value="">{t('all')}</option>
              <option value="in">{t('dir_in')}</option>
              <option value="out">{t('dir_out')}</option>
            </Select>
          </Field>
        )}
        <Field label={t('min_amount')} className="w-32">
          <Input inputMode="decimal" value={value.min_amount ?? ''} onChange={(e) => set({ min_amount: e.target.value || undefined })} />
        </Field>
        <Button variant="ghost" onClick={() => setOpen(!open)} className="h-10">
          <SlidersHorizontal className="size-4" />
          {t('filters')}
          {active > 0 && <span className="rounded-full bg-accent px-1.5 text-[10px] text-accent-ink">{active}</span>}
        </Button>
      </div>
      {open && (
        <div className="flex flex-wrap items-end gap-2 rounded-lg bg-surface-2 p-3">
          <Field label={t('max_amount')} className="w-32">
            <Input inputMode="decimal" value={value.max_amount ?? ''} onChange={(e) => set({ max_amount: e.target.value || undefined })} />
          </Field>
          <Field label={t('from_date')} className="w-40">
            <Input type="date" value={value.start?.slice(0, 10) ?? ''} onChange={(e) => set({ start: e.target.value ? `${e.target.value}T00:00:00Z` : undefined })} />
          </Field>
          <Field label={t('to_date')} className="w-40">
            <Input type="date" value={value.end?.slice(0, 10) ?? ''} onChange={(e) => set({ end: e.target.value ? `${e.target.value}T23:59:59Z` : undefined })} />
          </Field>
          <div className="pb-2">
            <Toggle checked={value.hide_spam !== false} onChange={(v) => set({ hide_spam: v })} label={t('hide_spam')} />
          </div>
          <Button variant="ghost" size="sm" onClick={() => onChange({})}>
            {t('clear')}
          </Button>
        </div>
      )}
    </div>
  )
}
