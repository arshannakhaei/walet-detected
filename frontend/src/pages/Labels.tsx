import { useQuery } from '@tanstack/react-query'
import { Pencil, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Address } from '../components/Address'
import { CATEGORIES, LabelDialog } from '../components/dialogs'
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Modal, Select, Spinner, TableWrap, td, th } from '../components/ui'
import { categoryTone } from '../components/badges'
import { api, type Chain, type Label, type LabelCategory } from '../lib/api'
import { ALL_CHAINS, CHAINS } from '../lib/chains'
import { fmtNumber } from '../lib/format'
import { useI18n, type TKey } from '../lib/i18n'

const PAGE = 100

export function Labels() {
  const { t, lang } = useI18n()
  const [chain, setChain] = useState<Chain | ''>('')
  const [category, setCategory] = useState<LabelCategory | ''>('')
  const [source, setSource] = useState<'' | 'builtin' | 'user'>('')
  const [search, setSearch] = useState('')
  const [limit, setLimit] = useState(PAGE)
  const [editing, setEditing] = useState<Label | null>(null)
  const [adding, setAdding] = useState(false)
  const [newAddr, setNewAddr] = useState({ chain: 'tron' as Chain, address: '' })
  const [target, setTarget] = useState<{ chain: Chain; address: string } | null>(null)
  const q = useQuery({ queryKey: ['labels', chain || undefined], queryFn: () => api.labels(chain || undefined) })

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return (q.data ?? []).filter(
      (l) =>
        (!category || l.category === category) &&
        (!source || l.source === source) &&
        (!needle || l.address.toLowerCase().includes(needle) || l.name.toLowerCase().includes(needle)),
    )
  }, [q.data, category, source, search])

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">{t('labels_title')}</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-2">{t('labels_intro')}</p>
        </div>
        <Button variant="primary" onClick={() => setAdding(true)}>
          <Plus className="size-4" />
          {t('add_label')}
        </Button>
      </div>

      <Card>
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-end gap-2">
            <Field label={t('chain')} className="w-36">
              <Select value={chain} onChange={(e) => setChain(e.target.value as Chain | '')}>
                <option value="">{t('all')}</option>
                {ALL_CHAINS.map((c) => (
                  <option key={c} value={c}>
                    {CHAINS[c].name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t('category')} className="w-40">
              <Select value={category} onChange={(e) => setCategory(e.target.value as LabelCategory | '')}>
                <option value="">{t('all')}</option>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {t(`cat_${c}` as TKey)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t('source')} className="w-32">
              <Select value={source} onChange={(e) => setSource(e.target.value as '' | 'builtin' | 'user')}>
                <option value="">{t('all')}</option>
                <option value="builtin">{t('builtin')}</option>
                <option value="user">{t('user')}</option>
              </Select>
            </Field>
            <Field label={t('search_table')} className="min-w-48 flex-1">
              <Input value={search} onChange={(e) => setSearch(e.target.value)} />
            </Field>
          </div>
          <div className="text-xs text-muted">{fmtNumber(rows.length, lang)}</div>
          {q.isPending ? (
            <Spinner />
          ) : q.isError ? (
            <ErrorBox error={q.error} />
          ) : rows.length === 0 ? (
            <Empty />
          ) : (
            <>
              <TableWrap>
                <thead>
                  <tr>
                    <th className={th}>{t('address')}</th>
                    <th className={th}>{t('name')}</th>
                    <th className={th}>{t('category')}</th>
                    <th className={th}>{t('source')}</th>
                    <th className={th} />
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, limit).map((l) => (
                    <tr key={`${l.chain}:${l.address}`} className="hover:bg-surface-2">
                      <td className={td}>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-xs text-muted">{CHAINS[l.chain].name}</span>
                          <Address address={l.address} chain={l.chain} />
                        </div>
                      </td>
                      <td className={td}>
                        {l.name}
                        {l.note && <div className="text-xs text-muted">{l.note}</div>}
                      </td>
                      <td className={td}>
                        <Badge tone={categoryTone(l.category)}>{t(`cat_${l.category}` as TKey)}</Badge>
                      </td>
                      <td className={`${td} text-xs text-ink-2`}>{t(l.source)}</td>
                      <td className={td}>
                        <button className="rounded p-1 text-muted hover:bg-surface hover:text-ink" onClick={() => setEditing(l)} title={t('label_it')}>
                          <Pencil className="size-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </TableWrap>
              {rows.length > limit && (
                <Button onClick={() => setLimit(limit + PAGE)} className="self-center">
                  {t('next')} ({fmtNumber(rows.length - limit, lang)})
                </Button>
              )}
            </>
          )}
        </div>
      </Card>

      {editing && <LabelDialog open onClose={() => setEditing(null)} chain={editing.chain} address={editing.address} current={editing} />}
      <Modal open={adding} onClose={() => setAdding(false)} title={t('add_label')}>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            setAdding(false)
            setTarget({ chain: newAddr.chain, address: newAddr.address.trim() })
          }}
        >
          <Field label={t('chain')}>
            <Select value={newAddr.chain} onChange={(e) => setNewAddr({ ...newAddr, chain: e.target.value as Chain })}>
              {ALL_CHAINS.map((c) => (
                <option key={c} value={c}>
                  {CHAINS[c].name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t('address')}>
            <Input className="mono" required value={newAddr.address} onChange={(e) => setNewAddr({ ...newAddr, address: e.target.value })} />
          </Field>
          <div className="flex justify-end">
            <Button type="submit" variant="primary">
              {t('next')}
            </Button>
          </div>
        </form>
      </Modal>
      {target && <LabelDialog open onClose={() => setTarget(null)} chain={target.chain} address={target.address} />}
    </div>
  )
}
