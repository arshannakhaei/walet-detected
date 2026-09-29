import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ExternalLink, KeyRound, XCircle } from 'lucide-react'
import { useState } from 'react'
import { api, type KeysIn, type KeyStatus } from '../lib/api'
import { fmtNumber } from '../lib/format'
import { useI18n, type TKey } from '../lib/i18n'
import { Button, Card, ErrorBox, Field, Input, Spinner } from './ui'

const FIELDS: { name: keyof KeysIn; label: TKey; url: string; secret: boolean }[] = [
  { name: 'trongrid_api_key', label: 'key_trongrid', url: 'https://www.trongrid.io/register', secret: true },
  { name: 'etherscan_api_key', label: 'key_etherscan', url: 'https://etherscan.io/apis', secret: true },
  { name: 'coingecko_api_key', label: 'key_coingecko', url: 'https://www.coingecko.com/en/api', secret: true },
  { name: 'solana_rpc_url', label: 'key_solana', url: 'https://www.helius.dev', secret: false },
]

function isSet(status: KeyStatus, name: keyof KeysIn): boolean {
  return name === 'solana_rpc_url' ? !status.solana_rpc_url.includes('api.mainnet-beta.solana.com') : status[name]
}

/** Enter free API keys; saved to .env and applied by the server without a restart. */
export function ApiKeysCard() {
  const { t, lang } = useI18n()
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ['settings'], queryFn: api.settings })
  const [values, setValues] = useState<KeysIn>({})
  const [saved, setSaved] = useState(false)

  const save = useMutation({
    mutationFn: () => api.saveKeys(Object.fromEntries(Object.entries(values).filter(([, v]) => v?.trim())) as KeysIn),
    onSuccess: (data) => {
      qc.setQueryData(['settings'], data)
      qc.invalidateQueries({ queryKey: ['chains'] })
      qc.invalidateQueries({ queryKey: ['health'] })
      setValues({})
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    },
  })

  return (
    <Card title={<span className="inline-flex items-center gap-2"><KeyRound className="size-4" />{t('api_keys')}</span>}>
      {status.isPending ? (
        <Spinner />
      ) : status.isError ? (
        <ErrorBox error={status.error} />
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate()
          }}
        >
          <p className="text-sm text-ink-2">{t('api_keys_intro')}</p>
          <div className="text-xs text-muted">
            {t('speed_now')}: Tron {fmtNumber(status.data.tron_requests_per_second, lang, 1)} · EVM{' '}
            {fmtNumber(status.data.evm_requests_per_second, lang, 1)} {t('per_second')}
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {FIELDS.map((f) => {
              const set = isSet(status.data, f.name)
              return (
                <Field
                  key={f.name}
                  label={
                    <span className="flex flex-wrap items-center gap-2">
                      {t(f.label)}
                      {set ? (
                        <span className="inline-flex items-center gap-1 text-good">
                          <CheckCircle2 className="size-3.5" />
                          {t('key_set')}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-muted">
                          <XCircle className="size-3.5" />
                          {t('key_not_set')}
                        </span>
                      )}
                      <a href={f.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent">
                        <ExternalLink className="size-3" />
                        {t('get_key')}
                      </a>
                    </span>
                  }
                >
                  <Input
                    id={`key-${f.name}`}
                    className="mono"
                    type={f.secret ? 'password' : 'url'}
                    autoComplete="off"
                    disabled={!status.data.can_edit}
                    placeholder={set ? t('key_placeholder_keep') : ''}
                    value={values[f.name] ?? ''}
                    onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
                  />
                </Field>
              )
            })}
          </div>
          {!status.data.can_edit && <p className="text-sm text-warning">{t('keys_remote')}</p>}
          {save.error && <ErrorBox error={save.error} />}
          <div>
            <Button
              type="submit"
              variant="primary"
              loading={save.isPending}
              disabled={!status.data.can_edit || !Object.values(values).some((v) => v?.trim())}
            >
              {saved ? t('keys_saved') : t('save_keys')}
            </Button>
          </div>
        </form>
      )}
    </Card>
  )
}
