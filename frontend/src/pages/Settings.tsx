import { CheckCircle2, ExternalLink, XCircle } from 'lucide-react'
import { CopyButton } from '../components/Address'
import { ApiKeysCard } from '../components/ApiKeysCard'
import { Card, Segmented, Spinner } from '../components/ui'
import { CHAINS } from '../lib/chains'
import { useChains } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import { useTheme } from '../lib/theme'

const MCP_CONFIG = `{
  "mcpServers": {
    "chaintrace": {
      "command": "C:\\\\path\\\\to\\\\walet-detected\\\\.venv\\\\Scripts\\\\python.exe",
      "args": ["C:\\\\path\\\\to\\\\walet-detected\\\\mcp_server.py"]
    }
  }
}`

export function Settings() {
  const { t, lang, setLang } = useI18n()
  const [theme, setTheme] = useTheme()
  const chains = useChains()

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-bold">{t('settings_title')}</h1>
      <Card>
        <div className="flex flex-wrap gap-8">
          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium text-ink-2">{t('language')}</span>
            <Segmented value={lang} onChange={setLang} options={[{ id: 'fa', label: 'فارسی' }, { id: 'en', label: 'English' }]} />
          </div>
          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium text-ink-2">{t('theme')}</span>
            <Segmented
              value={theme}
              onChange={setTheme}
              options={[
                { id: 'light', label: t('theme_light') },
                { id: 'dark', label: t('theme_dark') },
              ]}
            />
          </div>
        </div>
      </Card>

      <ApiKeysCard />

      <Card title={t('chains_status')}>
        {chains.isPending ? (
          <Spinner />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {chains.data?.map(({ chain, supported }) => (
              <div key={chain} className="flex items-center justify-between rounded-lg border border-line px-3 py-2 text-sm">
                <span className="flex items-center gap-2">
                  <span className="size-2.5 rounded-full" style={{ background: CHAINS[chain].color }} />
                  {CHAINS[chain].name}
                </span>
                {supported ? (
                  <span className="flex items-center gap-1 text-good">
                    <CheckCircle2 className="size-4" />
                    {t('enabled')}
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-muted">
                    <XCircle className="size-4" />
                    {t('disabled')}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
        <p className="mt-4 text-sm text-ink-2">{t('keys_help')}</p>
        <p className="mt-2 text-sm text-ink-2">{t('telegram_help')}</p>
        <a href="/docs" target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-sm text-accent hover:underline">
          <ExternalLink className="size-4" />
          {t('api_docs')}
        </a>
      </Card>

      <Card title={t('mcp_title')}>
        <p className="mb-3 text-sm text-ink-2">{t('mcp_help')}</p>
        <div className="relative">
          <pre className="mono scroll-thin overflow-x-auto rounded-lg bg-surface-2 p-3 text-xs">{MCP_CONFIG}</pre>
          <CopyButton text={MCP_CONFIG} className="absolute top-2 right-2" />
        </div>
      </Card>
    </div>
  )
}
