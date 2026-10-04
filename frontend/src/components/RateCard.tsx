import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Coins } from 'lucide-react'
import { useState } from 'react'
import { api } from '../lib/api'
import { fmtDate } from '../lib/format'
import { useI18n } from '../lib/i18n'
import { fmtToman, quoteStore, useCurrency, type CurrencyMode } from '../lib/money'
import { Badge, Button, Card, ErrorBox, Field, Input, Segmented } from './ui'

/** Today's dollar rate in toman, its source, and a manual override. */
export function RateCard() {
  const { t, lang } = useI18n()
  const qc = useQueryClient()
  const { mode, setMode } = useCurrency()
  const rate = useQuery({ queryKey: ['toman-rate'], queryFn: api.rates })
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings })
  const [value, setValue] = useState('')

  const save = useMutation({
    mutationFn: (v: string) => api.saveKeys({ usd_toman_rate: v }),
    onSuccess: (data) => {
      qc.setQueryData(['settings'], data)
      qc.invalidateQueries({ queryKey: ['toman-rate'] })
      quoteStore.reset()
      setValue('')
    },
  })

  const r = rate.data
  return (
    <Card
      title={
        <span className="inline-flex items-center gap-2">
          <Coins className="size-4" />
          {t('rate_title')}
        </span>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-6">
          <div>
            <div className="text-2xl font-bold tabular">{r?.rate ? fmtToman(Number(r.rate), lang, false) : '—'}</div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
              {t('per_dollar')}
              {r?.source && (
                <Badge tone={r.source === 'manual' ? 'warning' : 'good'}>
                  {r.source === 'manual' ? t('rate_manual') : `${t('rate_live')} · ${r.source === 'nobitex' ? 'Nobitex' : 'Wallex'}`}
                </Badge>
              )}
              {r?.updated_at && <span>{fmtDate(r.updated_at, lang)}</span>}
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium text-ink-2">{t('currency_label')}</span>
            <Segmented<CurrencyMode>
              value={mode}
              onChange={setMode}
              options={[
                { id: 'all', label: t('currency_all') },
                { id: 'toman', label: t('currency_toman') },
                { id: 'usd', label: t('currency_usd') },
                { id: 'token', label: t('currency_token') },
              ]}
            />
          </div>
        </div>
        {r && !r.rate && <p className="text-sm text-[var(--warning)]">{t('rate_none')}</p>}
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (value.trim()) save.mutate(value.trim())
          }}
        >
          <Field label={t('rate_input')} className="w-60">
            <Input
              inputMode="decimal"
              dir="ltr"
              placeholder={settings.data?.usd_toman_rate ?? '60000'}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              disabled={settings.data ? !settings.data.can_edit : false}
            />
          </Field>
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!value.trim()}>
            {t('rate_save')}
          </Button>
          {settings.data?.usd_toman_rate && (
            <Button type="button" variant="ghost" onClick={() => save.mutate('')}>
              {t('rate_clear')}
            </Button>
          )}
        </form>
        <p className="text-xs text-muted">{t('rate_input_help')}</p>
        {save.isError && <ErrorBox error={save.error} />}
      </div>
    </Card>
  )
}
