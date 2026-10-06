import { useMutation, useQuery } from '@tanstack/react-query'
import { Download, Info, Network, Play } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link as RouterLink, useSearchParams } from 'react-router-dom'
import { Address, TxLink } from '../components/Address'
import { SanctionBadges } from '../components/badges'
import { FlowGraph, Legend, type FlowEdge, type FlowGraphHandle, type FlowNode, type NodeKind } from '../components/FlowGraph'
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Select, Spinner, Stat, TableWrap, Textarea, Toggle, td, th } from '../components/ui'
import { api, type Chain, type Label, type LinkReport, type LinksRequest } from '../lib/api'
import { fmtAmount, fmtCompact, fmtDate, fmtNumber, shortAddr } from '../lib/format'
import { useLabelMap } from '../lib/hooks'
import { useI18n, type TKey } from '../lib/i18n'
import { Money, Worth } from '../lib/money'
import { readJson, writeJson } from '../lib/storage'

const RISKY = new Set(['mixer', 'sanctioned', 'scam'])
const TERMINAL = new Set(['exchange', 'bridge', 'defi', 'service', 'token_contract'])
const SENTENCE_LIMIT = 12
const ROW_LIMIT = 25

function splitAddresses(text: string): string[] {
  return [...new Set(text.split(/[\s,;]+/).map((a) => a.trim()).filter(Boolean))]
}

type Part = string | { v: string }

/** Template -> parts, so values (addresses, amounts) can be shown direction-isolated inside RTL text. */
function fill(template: string, values: Record<string, string | number>): Part[] {
  return template.split(/(\{\w+\})/).filter(Boolean).map((piece) => {
    const m = /^\{(\w+)\}$/.exec(piece)
    return m ? { v: String(values[m[1]] ?? '') } : piece
  })
}

/** "#3 (TQrY8t…4STm)" for listed wallets, short address otherwise. */
function useNamer(report: LinkReport | undefined) {
  return useMemo(() => {
    const index = new Map(report?.members.map((m) => [m.address, m.index]) ?? [])
    return (a: string) => {
      const i = index.get(a)
      return i ? `#${i} (${shortAddr(a, 5, 4)})` : shortAddr(a, 5, 4)
    }
  }, [report])
}

function buildSentences(r: LinkReport, name: (a: string) => string, t: (k: TKey) => string, lang: 'fa' | 'en') {
  const out: { text: Part[]; tone: 'strong' | 'normal' | 'weak'; tx?: string; money?: { amount: string; symbol: string; contract: string | null; at?: string } }[] = []
  for (const m of r.members) {
    if (m.likely_service)
      out.push({
        text: fill(t('links_s_service'), { who: name(m.address), n: fmtNumber(m.counterparty_count, lang) }),
        tone: 'weak',
      })
  }
  for (const m of r.members) {
    if (m.usdt_frozen || m.sanctioned)
      out.push({
        text: fill(t(m.usdt_frozen ? 'links_s_frozen' : 'links_s_sanctioned'), { who: name(m.address) }),
        tone: 'strong',
      })
  }
  for (const d of r.direct) {
    for (const tr of d.transfers.slice(0, 3)) {
      out.push({
        text: fill(t('links_s_direct'), {
          amount: fmtAmount(tr.amount, lang),
          token: d.token_symbol,
          from: name(d.from_address),
          to: name(d.to_address),
          date: fmtDate(tr.timestamp, lang),
        }),
        tone: 'strong',
        tx: tr.tx_hash,
        money: { amount: tr.amount, symbol: d.token_symbol, contract: d.token_contract, at: tr.timestamp },
      })
    }
  }
  for (const p of r.paths.filter((p) => !p.through_service)) {
    if (p.via.length === 1 && p.matched.length) {
      for (const m of p.matched.slice(0, 3)) {
        out.push({
          text: fill(t('links_s_path'), {
            amount: fmtAmount(m.incoming.amount, lang),
            token: p.token_symbol,
            from: name(p.from_address),
            via: name(p.via[0]),
            delay: fmtNumber(m.delay_minutes, lang),
            out: fmtAmount(m.outgoing.amount, lang),
            to: name(p.to_address),
          }),
          tone: 'strong',
          tx: m.outgoing.tx_hash,
          money: { amount: m.incoming.amount, symbol: p.token_symbol, contract: p.token_contract, at: m.incoming.timestamp },
        })
      }
    } else if (p.via.length === 1) {
      out.push({
        text: fill(t('links_s_path_total'), {
          from: name(p.from_address),
          in: fmtAmount(p.amount_in, lang),
          token: p.token_symbol,
          via: name(p.via[0]),
          out: fmtAmount(p.amount_out, lang),
          to: name(p.to_address),
        }),
        tone: 'normal',
        money: { amount: p.amount_out, symbol: p.token_symbol, contract: p.token_contract },
      })
    } else {
      out.push({
        text: fill(t('links_s_path_deep'), {
          from: name(p.from_address),
          via: p.via.map(name).join(' → '),
          to: name(p.to_address),
          out: fmtAmount(p.amount_out, lang),
          token: p.token_symbol,
        }),
        tone: 'normal',
      })
    }
  }
  for (const s of r.shared) {
    out.push({
      text: fill(t(s.role === 'common_source' ? 'links_s_shared_src' : 'links_s_shared_dst'), {
        via: name(s.address) + (s.label ? ` [${s.label.name}]` : ''),
        n: fmtNumber(s.members.length, lang),
        members: s.members.map((m) => name(m.address).split(' ')[0]).join('، '),
      }),
      tone: 'weak',
    })
  }
  return out
}

function buildGraph(r: LinkReport, labelOf: (a: string) => Label | null | undefined) {
  const nodes = new Map<string, FlowNode>()
  const index = new Map(r.members.map((m) => [m.address, m.index]))
  const kindOf = (a: string, member: boolean): NodeKind => {
    const label = labelOf(a)
    if (label && RISKY.has(label.category)) return 'risky'
    if (label && TERMINAL.has(label.category)) return 'exchange'
    return member ? 'root' : 'hub'
  }
  const add = (a: string) => {
    if (nodes.has(a)) return
    const i = index.get(a)
    nodes.set(a, {
      id: a,
      text: i ? `#${i}` : shortAddr(a, 4, 4),
      sub: i ? shortAddr(a, 4, 4) : labelOf(a)?.name,
      kind: kindOf(a, i !== undefined),
      layer: 0,
    })
  }
  const edges = new Map<string, FlowEdge>()
  const max = Math.max(1, ...r.direct.map((d) => Number(d.total)), ...r.paths.map((p) => Number(p.amount_out)))
  const addEdge = (from: string, to: string, amount: number, token: string, tone: FlowEdge['tone']) => {
    const id = `${from}>${to}:${token}`
    const prev = edges.get(id)
    if (prev) return
    edges.set(id, { id, source: from, target: to, text: `${fmtCompact(amount, 'en')} ${token}`, weight: Math.min(1, amount / max), tone })
  }
  for (const m of r.members) if (m.group !== null) add(m.address)
  for (const d of r.direct) {
    add(d.from_address)
    add(d.to_address)
    addEdge(d.from_address, d.to_address, Number(d.total), d.token_symbol, 'strong')
  }
  for (const p of r.paths.filter((p) => !p.through_service).slice(0, 60)) {
    const chain = [p.from_address, ...p.via, p.to_address]
    chain.forEach(add)
    for (let i = 0; i < chain.length - 1; i++) {
      const amount = i === 0 ? Number(p.amount_in) : Number(p.amount_out)
      addEdge(chain[i], chain[i + 1], amount, p.token_symbol, p.matched.length ? 'normal' : 'weak')
    }
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] }
}

function toCsv(r: LinkReport): string {
  const rows: string[][] = [['type', 'from', 'via', 'to', 'token', 'amount', 'count_or_delay_min', 'tx_hash', 'timestamp']]
  for (const d of r.direct)
    for (const tr of d.transfers) rows.push(['direct', d.from_address, '', d.to_address, d.token_symbol, tr.amount, '1', tr.tx_hash, tr.timestamp])
  for (const p of r.paths) {
    if (p.matched.length)
      for (const m of p.matched)
        rows.push(['via_matched', p.from_address, p.via.join(' > '), p.to_address, p.token_symbol, m.outgoing.amount, String(m.delay_minutes), m.outgoing.tx_hash, m.outgoing.timestamp])
    else
      rows.push([p.through_service ? 'via_service' : 'via_total', p.from_address, p.via.join(' > '), p.to_address, p.token_symbol, p.amount_out, '', '', ''])
  }
  for (const s of r.shared)
    for (const m of s.members) rows.push([s.role, s.role === 'common_source' ? s.address : m.address, '', s.role === 'common_source' ? m.address : s.address, s.token_symbol, m.amount, String(m.count), '', ''])
  return rows.map((row) => row.map((c) => (/[",\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c)).join(',')).join('\n')
}

function download(name: string, content: string, type: string) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([content], { type }))
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}

function useElapsed(running: boolean): number {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    if (!running) return
    const start = Date.now()
    const id = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000)
    return () => {
      clearInterval(id)
      setSeconds(0)
    }
  }, [running])
  return seconds
}

const stamp = () => new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')

function More({ total, open, onToggle }: { total: number; open: boolean; onToggle: () => void }) {
  const { t, lang } = useI18n()
  if (total <= ROW_LIMIT) return null
  return (
    <Button size="sm" variant="ghost" className="mt-2" onClick={onToggle}>
      {open ? t('links_show_less') : `${t('links_show_all')} (${fmtNumber(total, lang)})`}
    </Button>
  )
}

export function Links() {
  const { t, lang } = useI18n()
  const [params, setParams] = useSearchParams()
  const jobId = params.get('job')
  const [form, setForm] = useState<LinksRequest>(() => ({
    addresses: readJson<string>('ct-links-addresses', ''),
    token: 'USDT',
    min_amount: '1',
    deep: false,
    match_window_hours: 72,
  }))
  const set = (patch: Partial<LinksRequest>) => setForm((f) => ({ ...f, ...patch }))
  const count = splitAddresses(form.addresses).length

  const start = useMutation({
    mutationFn: (body: LinksRequest) => api.startLinks(body),
    onSuccess: (job) => setParams({ job: job.id }, { replace: false }),
  })
  const job = useQuery({
    queryKey: ['links', jobId],
    queryFn: () => api.linkJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data?.state === 'running' ? 1000 : false),
  })
  const running = start.isPending || job.data?.state === 'running'
  const elapsed = useElapsed(running)
  const report = job.data?.state === 'done' ? job.data.result ?? undefined : undefined

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold">{t('links_title')}</h1>
        <p className="mt-1 text-sm text-ink-2">{t('links_intro')}</p>
      </div>

      <Card>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            writeJson('ct-links-addresses', form.addresses)
            start.mutate({ ...form, token: form.token || null })
          }}
        >
          <Field label={`${t('links_addresses')} (${fmtNumber(count, lang)} ${t('links_count')})`}>
            <Textarea
              className="mono min-h-48 text-xs"
              dir="ltr"
              required
              spellCheck={false}
              placeholder={'TQrY8tryqsYVCYS3MFbtffiPp2ccyn4STm\nTTgdEUBD37S1NkfWQ7y5PYtUk4etpLefvD\n...'}
              value={form.addresses}
              onChange={(e) => set({ addresses: e.target.value })}
            />
          </Field>
          <div className="flex flex-wrap items-end gap-3">
            <Field label={t('token')} className="w-40">
              <Select value={form.token ?? ''} onChange={(e) => set({ token: e.target.value })}>
                <option value="USDT">USDT</option>
                <option value="USDC">USDC</option>
                <option value="TRX">TRX</option>
                <option value="ETH">ETH</option>
                <option value="">{t('links_token_all')}</option>
              </Select>
            </Field>
            <Field label={t('min_amount')} className="w-28">
              <Input inputMode="decimal" value={form.min_amount} onChange={(e) => set({ min_amount: e.target.value || '0' })} />
            </Field>
            <Field label={t('links_window')} className="w-36">
              <Input type="number" min={1} max={2160} value={form.match_window_hours} onChange={(e) => set({ match_window_hours: Number(e.target.value) || 72 })} />
            </Field>
            <div className="pb-2">
              <Toggle checked={form.deep} onChange={(deep) => set({ deep })} label={t('links_deep')} />
            </div>
          </div>
          <div className="flex items-start gap-2 text-xs text-muted">
            <Info className="mt-0.5 size-3.5 shrink-0" />
            {t('links_explain')}
          </div>
          <div>
            <Button type="submit" variant="primary" loading={running} disabled={count < 2}>
              <Play className="size-4" />
              {t('links_run')}
            </Button>
          </div>
        </form>
      </Card>

      {start.isError && <ErrorBox error={start.error} />}
      {job.isError && <ErrorBox error={job.error} />}
      {job.data?.state === 'failed' && <ErrorBox error={new Error(job.data.error ?? 'failed')} />}

      {running && (
        <Card>
          <div className="flex flex-col gap-2">
            <Spinner
              label={`${t(job.data?.stage === 'intermediaries' ? 'links_progress_intermediaries' : 'links_progress_members')} — ${fmtNumber(job.data?.done ?? 0, lang)} / ${fmtNumber(job.data?.total ?? count, lang)} · ${fmtNumber(elapsed, lang)}s`}
            />
            <div className="h-2 rounded-full bg-surface-2">
              <div
                className="h-full rounded-full bg-accent transition-all"
                style={{ width: `${job.data?.total ? (job.data.done / job.data.total) * 100 : 3}%` }}
              />
            </div>
            {elapsed > 8 && (
              <p className="text-xs text-muted">
                {t('links_slow_hint')}{' '}
                <RouterLink to="/settings" className="text-accent hover:underline">
                  {t('nav_settings')}
                </RouterLink>
              </p>
            )}
          </div>
        </Card>
      )}

      {report && <Results report={report} />}
    </div>
  )
}

function Results({ report: r }: { report: LinkReport }) {
  const { t, lang } = useI18n()
  const labels = useLabelMap(r.chain)
  const labelOf = (a: string) => labels.get(a) ?? r.members.find((m) => m.address === a)?.label ?? null
  const name = useNamer(r)
  const sentences = useMemo(() => buildSentences(r, name, t, lang), [r, name, t, lang])
  const graph = useMemo(() => buildGraph(r, (a) => labels.get(a)), [r, labels])
  const graphRef = useRef<FlowGraphHandle>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }))
  const linked = r.members.filter((m) => m.linked_members > 0).length
  const strongPaths = r.paths.filter((p) => !p.through_service)
  const nothing = r.direct.length === 0 && r.paths.length === 0 && r.shared.length === 0

  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label={t('links_members')} value={fmtNumber(r.members.length, lang)} />
        <Stat label={t('links_linked_members')} value={fmtNumber(linked, lang)} />
        <Stat label={t('links_direct')} value={fmtNumber(r.direct.length, lang)} />
        <Stat label={t('links_paths')} value={fmtNumber(strongPaths.length, lang)} />
        <Stat label={t('links_groups')} value={fmtNumber(r.groups.length, lang)} />
      </div>

      {!r.complete && (
        <div className="rounded-lg border border-[var(--warning)] bg-surface p-3 text-sm text-ink-2">{t('links_incomplete')}</div>
      )}

      <Card
        title={t('links_findings')}
        actions={
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => download(`links_${stamp()}.csv`, '﻿' + toCsv(r), 'text/csv')}>
              <Download className="size-4" />
              CSV
            </Button>
            <Button size="sm" variant="ghost" onClick={() => download(`links_${stamp()}.json`, JSON.stringify(r, null, 2), 'application/json')}>
              <Download className="size-4" />
              JSON
            </Button>
          </div>
        }
      >
        {nothing ? (
          <Empty>{t('links_none')}</Empty>
        ) : (
          <>
            <ul className="flex flex-col gap-2 text-sm">
              {(open.sentences ? sentences : sentences.slice(0, SENTENCE_LIMIT)).map((s, i) => (
                <li key={i} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span
                    className="mt-1.5 size-2 shrink-0 rounded-full"
                    style={{ background: s.tone === 'strong' ? 'var(--accent)' : s.tone === 'normal' ? 'var(--violet)' : 'var(--muted)' }}
                  />
                  <span className="min-w-0 flex-1 leading-7">
                    {s.text.map((part, j) =>
                      typeof part === 'string' ? (
                        part
                      ) : (
                        <bdi key={j} className="mx-0.5 rounded bg-surface-2 px-1 font-medium tabular">
                          {part.v}
                        </bdi>
                      ),
                    )}
                  </span>
                  {s.money && <Worth {...s.money} chain={r.chain} />}
                  {s.tx && <TxLink hash={s.tx} chain={r.chain} />}
                </li>
              ))}
            </ul>
            {sentences.length > SENTENCE_LIMIT && (
              <Button size="sm" variant="ghost" className="mt-2" onClick={() => toggle('sentences')}>
                {open.sentences ? t('links_show_less') : `${t('links_show_all')} (${fmtNumber(sentences.length, lang)})`}
              </Button>
            )}
          </>
        )}
      </Card>

      {graph.nodes.length > 0 && (
        <Card
          title={
            <span className="inline-flex items-center gap-2">
              <Network className="size-4" />
              {t('tab_graph')}
            </span>
          }
          actions={
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                const data = graphRef.current?.png()
                if (!data) return
                const a = document.createElement('a')
                a.href = data
                a.download = `links_${stamp()}.png`
                a.click()
              }}
            >
              <Download className="size-4" />
              {t('png')}
            </Button>
          }
        >
          <FlowGraph ref={graphRef} nodes={graph.nodes} edges={graph.edges} layout={graph.nodes.length <= 40 ? 'circle' : 'force'} height={520} />
          <div className="mt-3">
            <Legend
              items={[
                { kind: 'root', text: t('links_members') },
                { kind: 'hub', text: t('links_via') },
                { kind: 'exchange', text: t('legend_exchange') },
                { kind: 'risky', text: t('legend_risky') },
              ]}
            />
          </div>
        </Card>
      )}

      <Card title={t('links_members')}>
        <TableWrap>
          <thead>
            <tr>
              <th className={th}>#</th>
              <th className={th}>{t('address')}</th>
              <th className={th}>{t('links_group')}</th>
              <th className={th}>{t('links_linked')}</th>
              <th className={th}>{t('links_sent')}</th>
              <th className={th}>{t('links_received')}</th>
              <th className={th}>{t('links_transfers')}</th>
            </tr>
          </thead>
          <tbody>
            {r.members.map((m) => (
              <tr key={m.address}>
                <td className={`${td} tabular`}>{fmtNumber(m.index, lang)}</td>
                <td className={td}>
                  <Address address={m.address} chain={r.chain} label={labelOf(m.address)} />
                  <SanctionBadges status={m} />
                  {m.likely_service && (
                    <Badge tone="violet" className="ms-1" title={t('links_service_help')}>
                      {t('links_service')}
                    </Badge>
                  )}
                  {m.error && <div className="mt-1 text-xs text-[var(--critical)]">{m.error}</div>}
                </td>
                <td className={td}>
                  {m.group ? <Badge tone="accent">{`${t('links_group')} ${fmtNumber(m.group, lang)}`}</Badge> : <span className="text-xs text-muted">{t('links_no_group')}</span>}
                </td>
                <td className={`${td} tabular`}>{fmtNumber(m.linked_members, lang)}</td>
                <td className={`${td} tabular`}>
                  {Number(m.sent_to_members) ? <Money amount={m.sent_to_members} symbol={r.token ?? ''} chain={r.chain} showSymbol={false} /> : '—'}
                </td>
                <td className={`${td} tabular`}>
                  {Number(m.received_from_members) ? <Money amount={m.received_from_members} symbol={r.token ?? ''} chain={r.chain} showSymbol={false} /> : '—'}
                </td>
                <td className={`${td} tabular`}>
                  {fmtNumber(m.transfer_count, lang)} {m.truncated && <Badge tone="warning">{t('links_cut')}</Badge>}
                </td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      </Card>

      {r.direct.length > 0 && (
        <Card title={`${t('links_direct')} (${fmtNumber(r.direct.length, lang)})`}>
          <TableWrap>
            <thead>
              <tr>
                <th className={th}>{t('address')}</th>
                <th className={th}>{t('amount')}</th>
                <th className={th}>{t('tx')}</th>
              </tr>
            </thead>
            <tbody>
              {(open.direct ? r.direct : r.direct.slice(0, ROW_LIMIT)).map((d) => (
                <tr key={`${d.from_address}>${d.to_address}:${d.token_symbol}`}>
                  <td className={td}>
                    <FromTo from={d.from_address} to={d.to_address} chain={r.chain} name={name} labelOf={labelOf} />
                  </td>
                  <td className={`${td} tabular`}>
                    <Money amount={d.total} symbol={d.token_symbol} contract={d.token_contract} chain={r.chain} strong />
                    <div className="text-xs text-muted">
                      {fmtNumber(d.count, lang)} {t('links_transfers')} · {fmtDate(d.first_seen, lang, false)}
                      {d.first_seen.slice(0, 10) !== d.last_seen.slice(0, 10) && ` – ${fmtDate(d.last_seen, lang, false)}`}
                    </div>
                  </td>
                  <td className={td}>
                    <div className="flex flex-col gap-1">
                      {d.transfers.slice(0, open[`d${d.from_address}${d.to_address}`] ? 50 : 3).map((tr) => (
                        <span key={tr.tx_hash} className="flex flex-wrap items-center gap-x-2 text-xs">
                          <Money amount={tr.amount} symbol={d.token_symbol} contract={d.token_contract} chain={r.chain} at={tr.timestamp} showSymbol={false} />
                          <TxLink hash={tr.tx_hash} chain={r.chain} />
                          <span className="text-muted">{fmtDate(tr.timestamp, lang)}</span>
                        </span>
                      ))}
                      {d.transfers.length > 3 && (
                        <button type="button" className="text-start text-xs text-accent" onClick={() => toggle(`d${d.from_address}${d.to_address}`)}>
                          {open[`d${d.from_address}${d.to_address}`] ? t('links_show_less') : `${t('links_show_all')} (${fmtNumber(d.transfers.length, lang)})`}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
          <More total={r.direct.length} open={!!open.direct} onToggle={() => toggle('direct')} />
        </Card>
      )}

      {r.paths.length > 0 && (
        <Card title={`${t('links_paths')} (${fmtNumber(r.paths.length, lang)})`}>
          <TableWrap>
            <thead>
              <tr>
                <th className={th}>{t('address')}</th>
                <th className={th}>{t('links_via')}</th>
                <th className={th}>{t('amount')}</th>
                <th className={th}>{t('links_matched')}</th>
              </tr>
            </thead>
            <tbody>
              {(open.paths ? r.paths : r.paths.slice(0, ROW_LIMIT)).map((p, i) => (
                <tr key={i} className={p.through_service ? 'opacity-70' : undefined}>
                  <td className={td}>
                    <FromTo from={p.from_address} to={p.to_address} chain={r.chain} name={name} labelOf={labelOf} />
                  </td>
                  <td className={td}>
                    <div className="flex flex-col gap-0.5">
                      {p.via.map((v, j) => (
                        <Address key={v} address={v} chain={r.chain} label={labels.get(v) ?? p.via_labels[j]} />
                      ))}
                      {p.through_service && <Badge tone="neutral">{t('links_service_path')}</Badge>}
                    </div>
                  </td>
                  <td className={`${td} tabular text-xs`}>
                    <div>
                      ↓ <Money amount={p.amount_in} symbol={p.token_symbol} contract={p.token_contract} chain={r.chain} />
                    </div>
                    <div>
                      ↓ <Money amount={p.amount_out} symbol={p.token_symbol} contract={p.token_contract} chain={r.chain} />
                    </div>
                  </td>
                  <td className={td}>
                    {p.matched.length === 0 ? (
                      <span className="text-xs text-muted">—</span>
                    ) : (
                      <div className="flex flex-col gap-1">
                        {p.matched.slice(0, 3).map((m) => (
                          <span key={m.outgoing.tx_hash} className="flex flex-wrap items-center gap-x-2 text-xs">
                            <Badge tone="good">
                              {fmtAmount(m.incoming.amount, lang)} → {fmtAmount(m.outgoing.amount, lang)}
                            </Badge>
                            <span className="text-muted">
                              {fmtNumber(m.delay_minutes, lang)} {t('links_minutes')}
                            </span>
                            <TxLink hash={m.outgoing.tx_hash} chain={r.chain} />
                          </span>
                        ))}
                        {p.matched.length > 3 && <span className="text-xs text-muted">+{fmtNumber(p.matched.length - 3, lang)}</span>}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
          <More total={r.paths.length} open={!!open.paths} onToggle={() => toggle('paths')} />
        </Card>
      )}

      {r.shared.length > 0 && (
        <Card title={`${t('links_shared')} (${fmtNumber(r.shared.length, lang)})`}>
          <TableWrap>
            <thead>
              <tr>
                <th className={th}>{t('address')}</th>
                <th className={th}>{t('links_role')}</th>
                <th className={th}>{t('links_members')}</th>
              </tr>
            </thead>
            <tbody>
              {(open.shared ? r.shared : r.shared.slice(0, ROW_LIMIT)).map((s) => (
                <tr key={`${s.address}:${s.role}:${s.token_symbol}`}>
                  <td className={td}>
                    <Address address={s.address} chain={r.chain} label={labels.get(s.address) ?? s.label} />
                  </td>
                  <td className={td}>
                    <Badge tone={s.role === 'common_source' ? 'violet' : 'neutral'}>
                      {t(s.role === 'common_source' ? 'links_common_source' : 'links_common_destination')}
                    </Badge>
                  </td>
                  <td className={`${td} text-xs`}>
                    <div className="flex flex-col gap-0.5">
                      {s.members.map((m) => (
                        <span key={m.address} className="tabular">
                          <span className="mono">{name(m.address)}</span>: {fmtAmount(m.amount, lang)} {s.token_symbol}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
          <More total={r.shared.length} open={!!open.shared} onToggle={() => toggle('shared')} />
        </Card>
      )}
    </>
  )
}

function FromTo({
  from,
  to,
  chain,
  name,
  labelOf,
}: {
  from: string
  to: string
  chain: Chain
  name: (a: string) => string
  labelOf: (a: string) => Label | null
}): ReactNode {
  const tag = (a: string) => name(a).split(' ')[0]
  return (
    <div className="flex flex-col gap-0.5">
      <span className="inline-flex items-center gap-1">
        <b className="text-xs">{tag(from)}</b>
        <Address address={from} chain={chain} label={labelOf(from)} />
      </span>
      <span className="text-xs text-muted">↓</span>
      <span className="inline-flex items-center gap-1">
        <b className="text-xs">{tag(to)}</b>
        <Address address={to} chain={chain} label={labelOf(to)} />
      </span>
    </div>
  )
}
