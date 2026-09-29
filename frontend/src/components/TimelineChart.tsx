import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { TimelineRow } from '../lib/api'
import { fmtAmount, fmtCompact } from '../lib/format'
import { useI18n } from '../lib/i18n'
import { TableWrap, td, th } from './ui'

/** In vs out per period for one token: two series, fixed colors, one axis. */
export function TimelineChart({ rows, asTable }: { rows: TimelineRow[]; asTable: boolean }) {
  const { t, lang, dir } = useI18n()
  const data = rows.map((r) => ({
    period: r.period,
    in: Number(r.amount_in),
    out: Number(r.amount_out),
    count_in: r.count_in,
    count_out: r.count_out,
  }))

  if (asTable) {
    return (
      <TableWrap>
        <thead>
          <tr>
            <th className={th}>{t('period')}</th>
            <th className={th}>{t('inflow')}</th>
            <th className={th}>{t('outflow')}</th>
          </tr>
        </thead>
        <tbody>
          {[...data].reverse().map((d) => (
            <tr key={d.period}>
              <td className={`${td} tabular`}>{d.period}</td>
              <td className={`${td} tabular`}>
                {fmtAmount(d.in, lang)} <span className="text-muted">({d.count_in})</span>
              </td>
              <td className={`${td} tabular`}>
                {fmtAmount(d.out, lang)} <span className="text-muted">({d.count_out})</span>
              </td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    )
  }

  return (
    <div className="h-64 w-full" dir="ltr">
      <ResponsiveContainer>
        <BarChart data={data} barGap={2} barCategoryGap="20%" margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis
            dataKey="period"
            tick={{ fontSize: 11, fill: 'var(--muted)' }}
            tickLine={false}
            axisLine={{ stroke: 'var(--border)' }}
            reversed={dir === 'rtl'}
            minTickGap={16}
          />
          <YAxis
            tick={{ fontSize: 11, fill: 'var(--muted)' }}
            tickLine={false}
            axisLine={false}
            width={48}
            orientation={dir === 'rtl' ? 'right' : 'left'}
            tickFormatter={(v) => fmtCompact(v, lang)}
          />
          <Tooltip
            cursor={{ fill: 'var(--surface-2)' }}
            contentStyle={{
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 8,
              color: 'var(--text)',
              fontSize: 12,
            }}
            labelStyle={{ color: 'var(--text)', fontWeight: 700 }}
            formatter={(value, name) => [fmtAmount(Number(value), lang), name === 'in' ? t('inflow') : t('outflow')]}
          />
          <Legend
            iconType="circle"
            iconSize={8}
            formatter={(v) => <span style={{ color: 'var(--text-2)', fontSize: 12 }}>{v === 'in' ? t('inflow') : t('outflow')}</span>}
          />
          <Bar dataKey="in" fill="var(--series-in)" radius={[4, 4, 0, 0]} maxBarSize={28} />
          <Bar dataKey="out" fill="var(--series-out)" radius={[4, 4, 0, 0]} maxBarSize={28} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
