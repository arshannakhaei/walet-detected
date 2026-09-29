import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Briefcase, Eye, FileText, GitFork, LayoutDashboard, List, RefreshCw, ShieldAlert, Tag, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { Address } from '../../components/Address'
import { ChainBadge, RiskBadge } from '../../components/badges'
import { AddToCaseDialog, LabelDialog } from '../../components/dialogs'
import { Button, ErrorBox, Spinner, Tabs } from '../../components/ui'
import { api, walletPath, type Chain } from '../../lib/api'
import { useLabelMap } from '../../lib/hooks'
import { useI18n } from '../../lib/i18n'
import { rememberSearch } from '../../lib/search'
import { CounterpartiesTab } from './CounterpartiesTab'
import { GraphTab } from './GraphTab'
import { OverviewTab } from './OverviewTab'
import { RiskTab } from './RiskTab'
import { TransfersTab } from './TransfersTab'

type TabId = 'overview' | 'counterparties' | 'transfers' | 'graph' | 'risk'

export function WalletPage() {
  const { chain, address } = useParams() as { chain: Chain; address: string }
  const { t, lang } = useI18n()
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as TabId) || 'overview'
  const setTab = (id: TabId) => setParams({ tab: id }, { replace: true })
  const [labelOpen, setLabelOpen] = useState(false)
  const [caseOpen, setCaseOpen] = useState(false)
  const labels = useLabelMap(chain)
  const keys = useQuery({ queryKey: ['settings'], queryFn: api.settings, staleTime: 60_000 })

  const overview = useQuery({ queryKey: ['overview', chain, address], queryFn: () => api.overview(address, chain) })
  // The canonical form (e.g. lower-case EVM) as returned by the API.
  const addr = overview.data?.address ?? address
  const risk = useQuery({
    queryKey: ['risk', chain, addr, false],
    queryFn: () => api.risk(addr, chain, false),
    enabled: overview.isSuccess,
  })
  const watchlist = useQuery({ queryKey: ['watchlist'], queryFn: api.watchlist })
  const watched = watchlist.data?.some((w) => w.chain === chain && w.address === addr)

  useEffect(() => {
    if (overview.isSuccess) rememberSearch(chain, addr)
  }, [overview.isSuccess, chain, addr])

  const refresh = useMutation({
    mutationFn: () => api.refresh(addr, chain),
    onSuccess: () => qc.invalidateQueries({ predicate: (q) => q.queryKey.includes(addr) }),
  })
  const watch = useMutation({
    mutationFn: () => api.addWatch({ address: addr, chain }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const label = labels.get(addr)
  const tabs = [
    { id: 'overview' as const, label: t('tab_overview'), icon: <LayoutDashboard className="size-4" /> },
    { id: 'counterparties' as const, label: t('tab_counterparties'), icon: <Users className="size-4" /> },
    { id: 'transfers' as const, label: t('tab_transfers'), icon: <List className="size-4" /> },
    { id: 'graph' as const, label: t('tab_graph'), icon: <GitFork className="size-4" /> },
    { id: 'risk' as const, label: t('tab_risk'), icon: <ShieldAlert className="size-4" /> },
  ]

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
        <div className="flex flex-wrap items-center gap-2">
          <ChainBadge chain={chain} />
          {risk.data && <RiskBadge report={risk.data} />}
        </div>
        <div className="text-sm sm:text-base">
          <Address address={addr} chain={chain} label={label} full link={false} />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => refresh.mutate()} loading={refresh.isPending}>
            <RefreshCw className="size-3.5" />
            {t('refresh')}
          </Button>
          <Button size="sm" onClick={() => watch.mutate()} disabled={watched} loading={watch.isPending}>
            <Eye className="size-3.5" />
            {watched ? t('watching') : t('add_watch')}
          </Button>
          <Button size="sm" onClick={() => setLabelOpen(true)}>
            <Tag className="size-3.5" />
            {t('label_it')}
          </Button>
          <Button size="sm" onClick={() => setCaseOpen(true)}>
            <Briefcase className="size-3.5" />
            {t('add_to_case')}
          </Button>
          <a href={walletPath(addr, 'report', chain, { lang })} target="_blank" rel="noreferrer">
            <Button size="sm" tabIndex={-1}>
              <FileText className="size-3.5" />
              {t('report')}
            </Button>
          </a>
        </div>
      </div>

      <Tabs tabs={tabs} value={tab} onChange={setTab} />

      {overview.isPending && (
        <Spinner
          label={
            <span className="flex flex-col items-center gap-1 text-center">
              {t('loading_chain')}
              {chain === 'tron' && keys.data && !keys.data.trongrid_api_key && !keys.data.demo_mode && (
                <span className="text-xs text-muted">{t('slow_without_key')}</span>
              )}
            </span>
          }
        />
      )}
      {overview.isError && <ErrorBox error={overview.error} onRetry={() => overview.refetch()} />}
      {overview.data && (
        <>
          {tab === 'overview' && <OverviewTab overview={overview.data} risk={risk.data} onTab={setTab} />}
          {tab === 'counterparties' && <CounterpartiesTab chain={chain} address={addr} overview={overview.data} labels={labels} />}
          {tab === 'transfers' && <TransfersTab chain={chain} address={addr} overview={overview.data} labels={labels} />}
          {tab === 'graph' && <GraphTab chain={chain} address={addr} overview={overview.data} />}
          {tab === 'risk' && <RiskTab chain={chain} address={addr} />}
        </>
      )}

      <LabelDialog open={labelOpen} onClose={() => setLabelOpen(false)} chain={chain} address={addr} current={label} />
      <AddToCaseDialog open={caseOpen} onClose={() => setCaseOpen(false)} item={{ kind: 'address', chain, address: addr }} />
    </div>
  )
}
