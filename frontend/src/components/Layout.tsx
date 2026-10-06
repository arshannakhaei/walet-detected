import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { Bell, Briefcase, Coins, Eye, FileText, Languages, Moon, Network, Route, Search, Settings, Sun, Tags } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { resolveSearch } from '../lib/search'
import { useI18n, type TKey } from '../lib/i18n'
import { useCurrency, type CurrencyMode } from '../lib/money'
import { useTheme } from '../lib/theme'

const NAV: { to: string; key: TKey; icon: typeof Search }[] = [
  { to: '/', key: 'nav_search', icon: Search },
  { to: '/trace', key: 'nav_trace', icon: Route },
  { to: '/links', key: 'nav_links', icon: Network },
  { to: '/investigation', key: 'nav_investigation', icon: FileText },
  { to: '/cases', key: 'nav_cases', icon: Briefcase },
  { to: '/watchlist', key: 'nav_watchlist', icon: Eye },
  { to: '/labels', key: 'nav_labels', icon: Tags },
  { to: '/settings', key: 'nav_settings', icon: Settings },
]

function Logo() {
  return (
    <Link to="/" className="flex items-center gap-2 font-bold text-ink">
      <img src="/favicon.svg" alt="" className="size-7" />
      <span>ChainTrace</span>
    </Link>
  )
}

function HeaderSearch() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [value, setValue] = useState('')
  return (
    <form
      className="relative hidden max-w-xl flex-1 md:block"
      onSubmit={async (e) => {
        e.preventDefault()
        const target = await resolveSearch(value)
        if (target) {
          navigate(target)
          setValue('')
        } else {
          navigate(`/?q=${encodeURIComponent(value)}`)
        }
      }}
    >
      <Search className="pointer-events-none absolute top-1/2 size-4 -translate-y-1/2 text-muted start-3" />
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={t('search_placeholder')}
        className="h-10 w-full rounded-lg border border-line bg-surface-2 text-sm text-ink placeholder:text-muted focus:border-accent focus:bg-surface focus:outline-none ps-9 pe-3"
      />
    </form>
  )
}

function CurrencySelect() {
  const { t } = useI18n()
  const { mode, setMode } = useCurrency()
  return (
    <label className="flex items-center gap-1 rounded-lg px-1 text-ink-2 hover:bg-surface-2" title={t('currency_label')}>
      <Coins className="hidden size-5 shrink-0 sm:block" />
      <select
        aria-label={t('currency_label')}
        value={mode}
        onChange={(e) => setMode(e.target.value as CurrencyMode)}
        className="h-9 max-w-24 cursor-pointer bg-transparent text-[11px] font-bold text-ink-2 focus:outline-none sm:max-w-none sm:text-xs"
      >
        <option value="all">{t('currency_all')}</option>
        <option value="toman">{t('currency_toman')}</option>
        <option value="usd">{t('currency_usd')}</option>
        <option value="token">{t('currency_token')}</option>
      </select>
    </label>
  )
}

function AlertsBell() {
  const { data } = useQuery({
    queryKey: ['alerts', 'unread'],
    queryFn: () => api.alerts(true),
    refetchInterval: 30_000,
  })
  const count = data?.length ?? 0
  return (
    <Link to="/watchlist" className="relative rounded-lg p-2 text-ink-2 hover:bg-surface-2" aria-label="alerts">
      <Bell className="size-5" />
      {count > 0 && (
        <span className="absolute -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-critical px-1 text-[10px] font-bold text-white end-0">
          {count > 99 ? '99+' : count}
        </span>
      )}
    </Link>
  )
}

export function Layout({ children }: { children: ReactNode }) {
  const { t, lang, setLang } = useI18n()
  const [theme, setTheme] = useTheme()

  return (
    <div className="min-h-screen">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 z-30 hidden w-56 flex-col border-line bg-surface p-4 start-0 border-e lg:flex">
        <Logo />
        <nav className="mt-8 flex flex-col gap-1">
          {NAV.map(({ to, key, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                  isActive ? 'bg-accent-soft text-accent' : 'text-ink-2 hover:bg-surface-2 hover:text-ink',
                )
              }
            >
              <Icon className="size-4" />
              {t(key)}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto text-xs text-muted">{t('tagline')}</div>
      </aside>

      <div className="lg:ps-56">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-surface/90 px-4 backdrop-blur">
          <div className="lg:hidden">
            <Logo />
          </div>
          <HeaderSearch />
          <div className="ms-auto flex items-center gap-1">
            <CurrencySelect />
            <AlertsBell />
            <button
              onClick={() => setLang(lang === 'fa' ? 'en' : 'fa')}
              className="flex items-center gap-1 rounded-lg p-2 text-xs font-bold text-ink-2 hover:bg-surface-2"
              aria-label={t('language')}
            >
              <Languages className="size-5" />
              {lang === 'fa' ? 'EN' : 'فا'}
            </button>
            <button
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              className="rounded-lg p-2 text-ink-2 hover:bg-surface-2"
              aria-label={t('theme')}
            >
              {theme === 'dark' ? <Sun className="size-5" /> : <Moon className="size-5" />}
            </button>
          </div>
        </header>

        <main className="mx-auto w-full max-w-7xl px-4 pt-5 pb-24 lg:pb-10">{children}</main>
      </div>

      {/* Mobile bottom navigation */}
      <nav className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-7 border-t border-line bg-surface lg:hidden">
        {NAV.map(({ to, key, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              clsx(
                'flex flex-col items-center gap-0.5 py-2 text-[10px] font-medium',
                isActive ? 'text-accent' : 'text-ink-2',
              )
            }
          >
            <Icon className="size-5" />
            <span className="max-w-full truncate px-0.5">{t(key)}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
