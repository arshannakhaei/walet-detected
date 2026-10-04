import { useMutation } from '@tanstack/react-query'
import { Download, Expand, ExternalLink, Maximize, Play, Tag } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Address } from '../../components/Address'
import { LabelDialog } from '../../components/dialogs'
import { FlowGraph, Legend, type FlowEdge, type FlowGraphHandle, type FlowNode, type NodeKind } from '../../components/FlowGraph'
import { Badge, Button, Card, ErrorBox, Field, Input, Segmented, Select, Spinner, Toggle } from '../../components/ui'
import { api, type Chain, type Graph, type GraphEdge, type GraphNode, type WalletOverview } from '../../lib/api'
import { fmtAmount, fmtCompact, fmtNumber, shortAddr } from '../../lib/format'
import { useLabelMap } from '../../lib/hooks'
import { useI18n, type TKey } from '../../lib/i18n'
import { Worth } from '../../lib/money'

const RISKY = new Set(['mixer', 'sanctioned', 'scam'])
const TERMINAL = new Set(['exchange', 'bridge', 'defi', 'service', 'token_contract'])

function kindOf(n: GraphNode, root: string): NodeKind {
  if (n.address === root) return 'root'
  if (n.label && RISKY.has(n.label.category)) return 'risky'
  if (n.label && TERMINAL.has(n.label.category)) return 'exchange'
  if (n.is_hub) return 'hub'
  return 'plain'
}

const edgeKey = (e: GraphEdge) => `${e.from_address}>${e.to_address}:${e.token_contract ?? e.token_symbol}`

/** Merge an expansion (a graph rooted at `at`) into the current graph. */
function merge(base: Graph, extra: Graph, at: GraphNode): Graph {
  const nodes = new Map(base.nodes.map((n) => [n.address, n]))
  for (const n of extra.nodes) {
    const existing = nodes.get(n.address)
    if (n.address === at.address) {
      nodes.set(n.address, { ...(existing ?? n), expanded: true, stop_reason: null, is_hub: n.is_hub })
    } else if (!existing) {
      nodes.set(n.address, { ...n, depth: at.depth + n.depth })
    }
  }
  const edges = new Map(base.edges.map((e) => [edgeKey(e), e]))
  for (const e of extra.edges) if (!edges.has(edgeKey(e))) edges.set(edgeKey(e), e)
  return { ...base, nodes: [...nodes.values()], edges: [...edges.values()], truncated: base.truncated || extra.truncated }
}

export function GraphTab({ chain, address, overview }: { chain: Chain; address: string; overview: WalletOverview }) {
  const { t, lang } = useI18n()
  const labels = useLabelMap(chain)
  const ref = useRef<FlowGraphHandle>(null)
  const [params, setParams] = useState({
    depth_in: 1,
    depth_out: 2,
    max_nodes: 80,
    max_children: 10,
    follow_time: true,
    token: overview.flows[0]?.token_symbol ?? '',
    min_amount: '',
  })
  const [graph, setGraph] = useState<Graph | null>(null)
  const [layout, setLayout] = useState<'layers' | 'force'>('layers')
  const [selected, setSelected] = useState<string | null>(null)
  const [labelFor, setLabelFor] = useState<string | null>(null)

  const build = useMutation({
    mutationFn: () =>
      api.graph(address, chain, { ...params, token: params.token || undefined, min_amount: params.min_amount || undefined }),
    onSuccess: (g) => {
      setGraph(g)
      setSelected(null)
    },
  })
  const expand = useMutation({
    mutationFn: (node: GraphNode) =>
      api.graph(node.address, chain, {
        depth_in: node.depth <= 0 ? 1 : 0,
        depth_out: node.depth >= 0 ? 1 : 0,
        max_nodes: 40,
        max_children: params.max_children,
        follow_time: params.follow_time,
        token: params.token || undefined,
        min_amount: params.min_amount || undefined,
      }).then((g) => ({ g, node })),
    onSuccess: ({ g, node }) => setGraph((cur) => (cur ? merge(cur, g, node) : g)),
  })

  // Build once on first open.
  useEffect(() => {
    build.mutate()
  }, [address, chain]) // eslint-disable-line react-hooks/exhaustive-deps

  const { nodes, edges } = useMemo(() => {
    if (!graph) return { nodes: [] as FlowNode[], edges: [] as FlowEdge[] }
    const max = Math.max(1, ...graph.edges.map((e) => Number(e.amount)))
    const nodes: FlowNode[] = graph.nodes.map((n) => {
      const label = labels.get(n.address) ?? n.label
      return {
        id: n.address,
        text: shortAddr(n.address),
        sub: label?.name?.slice(0, 26),
        kind: kindOf({ ...n, label }, graph.root),
        layer: n.depth,
        dim: n.address !== graph.root && !n.expanded,
      }
    })
    const edges: FlowEdge[] = graph.edges.map((e) => ({
      id: edgeKey(e),
      source: e.from_address,
      target: e.to_address,
      text: `${fmtCompact(e.amount, 'en')} ${e.token_symbol}`,
      weight: Math.log10(1 + (9 * Number(e.amount)) / max),
    }))
    return { nodes, edges }
  }, [graph, labels, lang])

  const node = graph?.nodes.find((n) => n.address === selected)
  const nodeEdges = graph?.edges.filter((e) => e.from_address === selected || e.to_address === selected) ?? []
  const set = (patch: Partial<typeof params>) => setParams((p) => ({ ...p, ...patch }))

  const downloadPng = () => {
    const data = ref.current?.png()
    if (!data) return
    const a = document.createElement('a')
    a.href = data
    a.download = `graph_${chain}_${address.slice(0, 10)}.png`
    a.click()
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            build.mutate()
          }}
        >
          <Field label={t('depth_in')} className="w-24">
            <Select value={params.depth_in} onChange={(e) => set({ depth_in: Number(e.target.value) })}>
              {[0, 1, 2, 3, 4].map((d) => (
                <option key={d}>{d}</option>
              ))}
            </Select>
          </Field>
          <Field label={t('depth_out')} className="w-24">
            <Select value={params.depth_out} onChange={(e) => set({ depth_out: Number(e.target.value) })}>
              {[0, 1, 2, 3, 4].map((d) => (
                <option key={d}>{d}</option>
              ))}
            </Select>
          </Field>
          <Field label={t('token')} className="w-28">
            <Select value={params.token} onChange={(e) => set({ token: e.target.value })}>
              <option value="">{t('all')}</option>
              {overview.flows.map((f) => (
                <option key={f.token_symbol}>{f.token_symbol}</option>
              ))}
            </Select>
          </Field>
          <Field label={t('min_amount')} className="w-28">
            <Input inputMode="decimal" value={params.min_amount} onChange={(e) => set({ min_amount: e.target.value })} />
          </Field>
          <Field label={t('max_children')} className="w-28">
            <Input type="number" min={1} max={100} value={params.max_children} onChange={(e) => set({ max_children: Number(e.target.value) })} />
          </Field>
          <Field label={t('max_nodes')} className="w-24">
            <Input type="number" min={5} max={500} value={params.max_nodes} onChange={(e) => set({ max_nodes: Number(e.target.value) })} />
          </Field>
          <div className="pb-2">
            <Toggle checked={params.follow_time} onChange={(v) => set({ follow_time: v })} label={t('follow_time')} />
          </div>
          <Button type="submit" variant="primary" loading={build.isPending}>
            <Play className="size-4" />
            {t('build_graph')}
          </Button>
        </form>
      </Card>

      {build.isError && <ErrorBox error={build.error} onRetry={() => build.mutate()} />}
      {expand.isError && <ErrorBox error={expand.error} />}

      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <Card
          title={
            graph ? (
              <span className="text-ink-2">
                {fmtNumber(graph.nodes.length, lang)} {t('nodes')} · {fmtNumber(graph.edges.length, lang)} {t('edges')}
              </span>
            ) : (
              t('tab_graph')
            )
          }
          actions={
            <>
              <Segmented
                value={layout}
                onChange={setLayout}
                options={[
                  { id: 'layers', label: t('layout_layers') },
                  { id: 'force', label: t('layout_force') },
                ]}
              />
              <Button size="sm" variant="ghost" onClick={() => ref.current?.fit()} title={t('fit')}>
                <Maximize className="size-4" />
              </Button>
              <Button size="sm" variant="ghost" onClick={downloadPng} title={t('png')}>
                <Download className="size-4" />
              </Button>
            </>
          }
        >
          {build.isPending && !graph ? (
            <Spinner label={t('loading_chain')} />
          ) : (
            <div className="flex flex-col gap-3">
              {graph?.truncated && <Badge tone="warning">{t('graph_truncated')}</Badge>}
              <div className="relative">
                <FlowGraph
                  ref={ref}
                  nodes={nodes}
                  edges={edges}
                  layout={layout}
                  selected={selected}
                  onSelect={setSelected}
                  onExpand={(id) => {
                    const n = graph?.nodes.find((x) => x.address === id)
                    if (n && id !== graph?.root) expand.mutate(n)
                  }}
                />
                {expand.isPending && (
                  <div className="absolute inset-x-0 top-2 flex justify-center">
                    <Badge tone="accent">{t('loading')}</Badge>
                  </div>
                )}
              </div>
              <Legend
                items={[
                  { kind: 'root', text: t('legend_root') },
                  { kind: 'plain', text: t('legend_plain') },
                  { kind: 'exchange', text: t('legend_exchange') },
                  { kind: 'risky', text: t('legend_risky') },
                  { kind: 'hub', text: t('legend_hub') },
                ]}
              />
              <p className="text-xs text-muted">{t('graph_hint')}</p>
            </div>
          )}
        </Card>

        <Card title={node ? shortAddr(node.address, 8, 6) : t('details')}>
          {!node ? (
            <p className="text-sm text-ink-2">{t('graph_hint')}</p>
          ) : (
            <div className="flex flex-col gap-3 text-sm">
              <Address address={node.address} chain={chain} label={labels.get(node.address) ?? node.label} full link={false} />
              <div className="flex flex-wrap gap-1.5">
                <Badge>
                  {node.depth === 0 ? t('legend_root') : node.depth < 0 ? `${t('senders')} ${-node.depth}` : `${t('receivers')} ${node.depth}`}
                </Badge>
                {node.is_hub && <Badge tone="violet">{t('reason_hub')}</Badge>}
                {node.stop_reason && node.stop_reason !== 'hub' && (
                  <Badge>
                    {t('not_expanded')}: {t(`reason_${node.stop_reason}` as TKey)}
                  </Badge>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <Link to={`/wallet/${chain}/${node.address}`}>
                  <Button size="sm" tabIndex={-1}>
                    <ExternalLink className="size-3.5" />
                    {t('open_wallet')}
                  </Button>
                </Link>
                {node.address !== graph?.root && (
                  <Button size="sm" onClick={() => expand.mutate(node)} loading={expand.isPending}>
                    <Expand className="size-3.5" />
                    {t('expand')}
                  </Button>
                )}
                <Button size="sm" onClick={() => setLabelFor(node.address)}>
                  <Tag className="size-3.5" />
                  {t('label_it')}
                </Button>
              </div>
              <ul className="flex flex-col divide-y divide-[var(--border)]">
                {nodeEdges.map((e) => {
                  const incoming = e.to_address === node.address
                  const other = incoming ? e.from_address : e.to_address
                  return (
                    <li key={edgeKey(e)} className="flex items-center justify-between gap-2 py-1.5">
                      <button className="mono truncate text-xs text-accent hover:underline" onClick={() => setSelected(other)}>
                        {incoming ? '← ' : '→ '}
                        {shortAddr(other)}
                      </button>
                      <span className="flex shrink-0 flex-col items-end text-xs">
                        <span className="tabular">
                          {fmtAmount(e.amount, lang)} {e.token_symbol} <span className="text-muted">×{e.count}</span>
                        </span>
                        <Worth amount={e.amount} symbol={e.token_symbol} contract={e.token_contract} chain={chain} />
                      </span>
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
        </Card>
      </div>
      {labelFor && <LabelDialog open onClose={() => setLabelFor(null)} chain={chain} address={labelFor} current={labels.get(labelFor)} />}
    </div>
  )
}
