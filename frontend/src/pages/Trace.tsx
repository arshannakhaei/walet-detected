import { useMutation } from '@tanstack/react-query'
import { Briefcase, Download, Info, Play } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Address, TxLink } from '../components/Address'
import { AddToCaseDialog } from '../components/dialogs'
import { FlowGraph, Legend, type FlowEdge, type FlowGraphHandle, type FlowNode, type NodeKind } from '../components/FlowGraph'
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Segmented, Select, Spinner, TableWrap, td, th } from '../components/ui'
import { api, type Chain, type EndReason, type Label, type TraceRequest, type TraceResult } from '../lib/api'
import { fmtAmount, fmtCompact, fmtDate, fmtPercent, shortAddr } from '../lib/format'
import { useLabelMap } from '../lib/hooks'
import { useI18n, type TKey } from '../lib/i18n'

const RISKY = new Set(['mixer', 'sanctioned', 'scam'])
const TERMINAL = new Set(['exchange', 'bridge', 'defi', 'service', 'token_contract'])

const REASON_KEY: Record<EndReason, TKey> = {
  unspent: 'reason_unspent',
  unknown_source: 'reason_unknown_source',
  labeled: 'reason_labeled_end',
  hub: 'reason_hub_end',
  max_hops: 'reason_max_hops',
  below_min: 'reason_below_min',
  pruned: 'reason_pruned',
  step_limit: 'reason_step_limit',
  loop: 'reason_loop',
}

function buildGraph(r: TraceResult, labelOf: (a: string) => Label | undefined) {
  const forward = r.direction === 'forward'
  const layer = new Map<string, number>()
  const origin = forward ? r.start.from_address : r.start.to_address
  layer.set(origin, 0)
  for (const f of [...r.flows].sort((a, b) => a.hop - b.hop)) {
    if (forward) {
      const from = layer.get(f.from_address) ?? 0
      if (!layer.has(f.from_address)) layer.set(f.from_address, from)
      if (!layer.has(f.to_address)) layer.set(f.to_address, from + 1)
    } else {
      const to = layer.get(f.to_address) ?? 0
      if (!layer.has(f.to_address)) layer.set(f.to_address, to)
      if (!layer.has(f.from_address)) layer.set(f.from_address, to - 1)
    }
  }
  const hubs = new Set(r.nodes.filter((n) => n.is_hub).map((n) => n.address))
  const nodes: FlowNode[] = r.nodes.map((n) => {
    const label = labelOf(n.address) ?? n.label
    let kind: NodeKind = 'plain'
    if (n.address === origin) kind = 'root'
    else if (label && RISKY.has(label.category)) kind = 'risky'
    else if (label && TERMINAL.has(label.category)) kind = 'exchange'
    else if (hubs.has(n.address)) kind = 'hub'
    return {
      id: n.address,
      text: shortAddr(n.address),
      sub: label?.name,
      kind,
      layer: layer.get(n.address) ?? 0,
    }
  })
  const total = Number(r.traced_amount) || 1
  const edges: FlowEdge[] = r.flows.map((f) => ({
    id: f.transfer_id,
    source: f.from_address,
    target: f.to_address,
    text: `${fmtCompact(f.traced_amount, 'en')} · ${fmtPercent(f.confidence, 'en')}`,
    weight: Math.min(1, Number(f.traced_amount) / total),
    tone: f.match === 'exact' || f.match === 'start' ? 'strong' : Number(f.confidence) < 0.5 ? 'weak' : 'normal',
  }))
  return { nodes, edges }
}

export function Trace() {
  const { t, lang } = useI18n()
  const [params] = useSearchParams()
  const [form, setForm] = useState<TraceRequest>({
    address: params.get('address') ?? '',
    tx_hash: params.get('tx') ?? '',
    chain: (params.get('chain') as Chain) || null,
    token: params.get('token') || null,
    amount: null,
    direction: (params.get('direction') as TraceRequest['direction']) || 'forward',
    method: 'fifo',
    max_hops: 6,
    min_amount: '1',
    max_branches: 10,
    tolerance: '0.01',
    exact_window_hours: 72,
  })
  const [advanced, setAdvanced] = useState(false)
  const [caseOpen, setCaseOpen] = useState(false)
  const graphRef = useRef<FlowGraphHandle>(null)
  const set = (patch: Partial<TraceRequest>) => setForm((f) => ({ ...f, ...patch }))

  const run = useMutation({ mutationFn: (body: TraceRequest) => api.trace(body) })
  const result = run.data
  const labels = useLabelMap(result?.chain)

  // Run immediately when opened from a transfer's "trace" button.
  useEffect(() => {
    if (form.address && form.tx_hash) run.mutate(form)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const graph = useMemo(() => (result ? buildGraph(result, (a) => labels.get(a)) : null), [result, labels])
  const summary = result
    ? (Object.entries(result.summary) as [EndReason, string][]).sort((a, b) => Number(b[1]) - Number(a[1]))
    : []
  const maxSummary = Math.max(1, ...summary.map(([, v]) => Number(v)))

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold">{t('trace_title')}</h1>
        <p className="mt-1 text-sm text-ink-2">{t('trace_intro')}</p>
      </div>

      <Card>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            run.mutate({ ...form, amount: form.amount || null, token: form.token || null })
          }}
        >
          <div className="grid gap-3 md:grid-cols-2">
            <Field label={t('trace_address')}>
              <Input className="mono" required value={form.address} onChange={(e) => set({ address: e.target.value.trim() })} />
            </Field>
            <Field label={t('tx_hash')}>
              <Input className="mono" required value={form.tx_hash} onChange={(e) => set({ tx_hash: e.target.value.trim() })} />
            </Field>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <Segmented
              value={form.direction}
              onChange={(direction) => set({ direction })}
              options={[
                { id: 'forward', label: t('trace_forward') },
                { id: 'backward', label: t('trace_backward') },
              ]}
            />
            <Field label={t('method')} className="w-56">
              <Select value={form.method} onChange={(e) => set({ method: e.target.value as TraceRequest['method'] })}>
                <option value="fifo">{t('fifo')}</option>
                <option value="lifo">{t('lifo')}</option>
              </Select>
            </Field>
            <Field label={t('max_hops')} className="w-24">
              <Input type="number" min={1} max={20} value={form.max_hops} onChange={(e) => set({ max_hops: Number(e.target.value) })} />
            </Field>
            <Field label={t('min_amount')} className="w-28">
              <Input inputMode="decimal" value={form.min_amount} onChange={(e) => set({ min_amount: e.target.value || '0' })} />
            </Field>
            <Button type="button" variant="ghost" onClick={() => setAdvanced(!advanced)}>
              {t('advanced')}
            </Button>
          </div>
          {advanced && (
            <div className="flex flex-wrap items-end gap-3 rounded-lg bg-surface-2 p-3">
              <Field label={t('partial_amount')} className="w-48">
                <Input inputMode="decimal" value={form.amount ?? ''} onChange={(e) => set({ amount: e.target.value || null })} />
              </Field>
              <Field label={t('token')} className="w-40">
                <Input value={form.token ?? ''} onChange={(e) => set({ token: e.target.value || null })} placeholder="USDT" />
              </Field>
              <Field label={t('tolerance')} className="w-40">
                <Input
                  type="number"
                  min={0}
                  max={50}
                  step={0.1}
                  value={Number(form.tolerance) * 100}
                  onChange={(e) => set({ tolerance: String(Number(e.target.value) / 100) })}
                />
              </Field>
              <Field label={t('max_branches')} className="w-40">
                <Input type="number" min={1} max={100} value={form.max_branches} onChange={(e) => set({ max_branches: Number(e.target.value) })} />
              </Field>
            </div>
          )}
          <div className="flex items-start gap-2 text-xs text-muted">
            <Info className="mt-0.5 size-3.5 shrink-0" />
            {t('trace_explain')}
          </div>
          <div>
            <Button type="submit" variant="primary" loading={run.isPending}>
              <Play className="size-4" />
              {t('run_trace')}
            </Button>
          </div>
        </form>
      </Card>

      {run.isPending && <Spinner label={t('loading_chain')} />}
      {run.isError && <ErrorBox error={run.error} />}

      {result && graph && (
        <>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card title={t('traced_amount')}>
              <div className="text-3xl font-bold tabular">
                {fmtAmount(result.traced_amount, lang)} <span className="text-lg text-ink-2">{result.token_symbol}</span>
              </div>
              <div className="mt-3 flex flex-col gap-1 text-sm">
                <TxLink hash={result.start.tx_hash} chain={result.chain} />
                <span className="text-xs text-muted">{fmtDate(result.start.timestamp, lang)}</span>
              </div>
              <Button className="mt-4" size="sm" onClick={() => setCaseOpen(true)}>
                <Briefcase className="size-3.5" />
                {t('save_to_case')}
              </Button>
            </Card>
            <Card title={t('where_ended')} className="lg:col-span-2">
              {summary.length === 0 ? (
                <Empty />
              ) : (
                <ul className="flex flex-col gap-2">
                  {summary.map(([reason, amount]) => (
                    <li key={reason} className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3 text-sm">
                      <span className="truncate">{t(REASON_KEY[reason])}</span>
                      <span className="h-2.5 rounded-full bg-surface-2">
                        <span
                          className="block h-full rounded-full bg-[var(--series-in)]"
                          style={{ width: `${(Number(amount) / maxSummary) * 100}%` }}
                        />
                      </span>
                      <span className="tabular text-end font-medium">
                        {fmtAmount(amount, lang)}{' '}
                        <span className="text-xs text-muted">({fmtPercent(Number(amount) / Number(result.traced_amount), lang)})</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <Card
            title={t('path')}
            actions={
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  const data = graphRef.current?.png()
                  if (!data) return
                  const a = document.createElement('a')
                  a.href = data
                  a.download = `trace_${result.start.tx_hash.slice(0, 10)}.png`
                  a.click()
                }}
              >
                <Download className="size-4" />
                {t('png')}
              </Button>
            }
          >
            <FlowGraph ref={graphRef} nodes={graph.nodes} edges={graph.edges} layout="layers" height={480} />
            <div className="mt-3">
              <Legend
                items={[
                  { kind: 'root', text: t('legend_root') },
                  { kind: 'plain', text: t('legend_plain') },
                  { kind: 'exchange', text: t('legend_exchange') },
                  { kind: 'risky', text: t('legend_risky') },
                  { kind: 'hub', text: t('legend_hub') },
                ]}
              />
            </div>
          </Card>

          <Card title={t('endpoints')}>
            <TableWrap>
              <thead>
                <tr>
                  <th className={th}>{t('address')}</th>
                  <th className={th}>{t('status')}</th>
                  <th className={th}>{t('amount')}</th>
                  <th className={th}>{t('confidence')}</th>
                </tr>
              </thead>
              <tbody>
                {result.endpoints.map((e) => (
                  <tr key={`${e.address}:${e.reason}`}>
                    <td className={td}>
                      <Address address={e.address} chain={result.chain} label={labels.get(e.address) ?? e.label} />
                    </td>
                    <td className={td}>
                      <Badge tone={e.reason === 'labeled' ? 'good' : e.reason === 'hub' ? 'violet' : 'neutral'}>{t(REASON_KEY[e.reason])}</Badge>
                    </td>
                    <td className={`${td} tabular font-medium`}>
                      {fmtAmount(e.amount, lang)} {result.token_symbol}
                    </td>
                    <td className={`${td} tabular`}>{fmtPercent(e.confidence, lang)}</td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
          </Card>

          <Card title={`${t('path')} (${result.flows.length})`}>
            <TableWrap>
              <thead>
                <tr>
                  <th className={th}>{t('hop')}</th>
                  <th className={th}>{t('address')}</th>
                  <th className={th}>{t('traced_amount')}</th>
                  <th className={th}>{t('confidence')}</th>
                  <th className={th}>{t('tx')}</th>
                </tr>
              </thead>
              <tbody>
                {result.flows.map((f) => (
                  <tr key={f.transfer_id}>
                    <td className={`${td} tabular`}>{f.hop}</td>
                    <td className={td}>
                      <div className="flex flex-col gap-0.5">
                        <Address address={f.from_address} chain={result.chain} label={labels.get(f.from_address)} />
                        <span className="text-xs text-muted">↓</span>
                        <Address address={f.to_address} chain={result.chain} label={labels.get(f.to_address)} />
                      </div>
                    </td>
                    <td className={`${td} tabular`}>
                      <b>{fmtAmount(f.traced_amount, lang)}</b>
                      <div className="text-xs text-muted">
                        {t('of')} {fmtAmount(f.transfer_amount, lang)}
                      </div>
                    </td>
                    <td className={td}>
                      <div className="tabular">{fmtPercent(f.confidence, lang)}</div>
                      <Badge tone={f.match === 'exact' ? 'good' : 'neutral'}>{f.match === 'exact' ? t('match_exact') : f.match}</Badge>
                    </td>
                    <td className={td}>
                      <TxLink hash={f.tx_hash} chain={result.chain} />
                      <div className="text-xs text-muted">{fmtDate(f.timestamp, lang)}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
          </Card>
          <AddToCaseDialog open={caseOpen} onClose={() => setCaseOpen(false)} item={{ kind: 'trace', data: result }} />
        </>
      )}
    </div>
  )
}
