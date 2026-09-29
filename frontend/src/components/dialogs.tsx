import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type Chain, type Label, type LabelCategory, type TraceResult } from '../lib/api'
import { useI18n, type TKey } from '../lib/i18n'
import { Button, ErrorBox, Field, Input, Modal, Select, Textarea } from './ui'

export const CATEGORIES: LabelCategory[] = [
  'exchange', 'bridge', 'mixer', 'defi', 'token_contract', 'sanctioned', 'scam', 'service', 'personal', 'other',
]

export function LabelDialog({
  open,
  onClose,
  chain,
  address,
  current,
}: {
  open: boolean
  onClose: () => void
  chain: Chain
  address: string
  current?: Label | null
}) {
  const { t } = useI18n()
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [category, setCategory] = useState<LabelCategory>('personal')
  const [note, setNote] = useState('')
  useEffect(() => {
    if (open) {
      setName(current?.name ?? '')
      setCategory(current?.category ?? 'personal')
      setNote(current?.note ?? '')
    }
  }, [open, current])

  const save = useMutation({
    mutationFn: () => api.setLabel(chain, address, { name, category, note: note || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['labels'] })
      onClose()
    },
  })
  const remove = useMutation({
    mutationFn: () => api.deleteLabel(chain, address),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['labels'] })
      onClose()
    },
  })

  return (
    <Modal open={open} onClose={onClose} title={t('label_it')}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <div className="mono break-all rounded-lg bg-surface-2 p-2 text-xs">{address}</div>
        <Field label={t('name')}>
          <Input value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} autoFocus />
        </Field>
        <Field label={t('category')}>
          <Select value={category} onChange={(e) => setCategory(e.target.value as LabelCategory)}>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {t(`cat_${c}` as TKey)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t('note')}>
          <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        {(save.error || remove.error) && <ErrorBox error={save.error || remove.error} />}
        <div className="flex flex-wrap justify-end gap-2">
          {current?.source === 'user' && (
            <Button type="button" variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
              {t('delete')}
            </Button>
          )}
          <Button type="button" variant="ghost" onClick={onClose}>
            {t('cancel')}
          </Button>
          <Button type="submit" variant="primary" loading={save.isPending}>
            {t('save')}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

/** Add an address or a trace result to an existing or new case. */
export function AddToCaseDialog({
  open,
  onClose,
  item,
}: {
  open: boolean
  onClose: () => void
  item: { kind: 'address'; chain: Chain; address: string } | { kind: 'trace'; data: TraceResult }
}) {
  const { t } = useI18n()
  const qc = useQueryClient()
  const cases = useQuery({ queryKey: ['cases'], queryFn: api.cases, enabled: open })
  const [caseId, setCaseId] = useState<string>('')
  const [newTitle, setNewTitle] = useState('')
  const [note, setNote] = useState('')
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (open) {
      setDone(false)
      setNote('')
      setNewTitle('')
    }
  }, [open])
  useEffect(() => {
    if (!caseId && cases.data?.length) setCaseId(String(cases.data[0].id))
  }, [cases.data, caseId])

  const save = useMutation({
    mutationFn: async () => {
      const id = newTitle.trim() ? (await api.createCase(newTitle.trim())).id : Number(caseId)
      if (item.kind === 'address') {
        await api.addCaseItem(id, { kind: 'address', chain: item.chain, address: item.address, note })
      } else {
        const title = `${item.data.traced_amount} ${item.data.token_symbol} · ${item.data.direction}`
        await api.addCaseItem(id, { kind: 'trace', data: item.data, title, note })
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cases'] })
      qc.invalidateQueries({ queryKey: ['case'] })
      setDone(true)
      setTimeout(onClose, 700)
    },
  })

  return (
    <Modal open={open} onClose={onClose} title={t('add_to_case')}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        {!!cases.data?.length && (
          <Field label={t('choose_case')}>
            <Select value={caseId} onChange={(e) => setCaseId(e.target.value)} disabled={!!newTitle.trim()}>
              {cases.data.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </Select>
          </Field>
        )}
        <Field label={cases.data?.length ? t('or_new_case') : t('new_case')}>
          <Input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder={t('case_title')} />
        </Field>
        <Field label={t('note')}>
          <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        {save.error && <ErrorBox error={save.error} />}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t('cancel')}
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={save.isPending}
            disabled={!newTitle.trim() && !caseId}
          >
            {done ? t('saved') : t('save')}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
