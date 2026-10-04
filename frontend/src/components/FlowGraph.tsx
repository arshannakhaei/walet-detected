import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { cssVar } from '../lib/theme'

export type NodeKind = 'root' | 'risky' | 'exchange' | 'hub' | 'plain'

export interface FlowNode {
  id: string
  text: string
  sub?: string
  kind: NodeKind
  layer: number // x position: negative = sources, positive = destinations
  dim?: boolean // e.g. not expanded
}

export interface FlowEdge {
  id: string
  source: string
  target: string
  text: string
  weight: number // 0..1, drives line width
  tone?: 'normal' | 'strong' | 'weak'
}

export interface FlowGraphHandle {
  png: () => string | null
  fit: () => void
}

const KIND_VAR: Record<NodeKind, string> = {
  root: '--accent',
  risky: '--critical',
  exchange: '--good',
  hub: '--violet',
  plain: '--node',
}

function layerPositions(nodes: FlowNode[]): Record<string, { x: number; y: number }> {
  const layers = new Map<number, FlowNode[]>()
  for (const n of nodes) {
    const list = layers.get(n.layer) ?? []
    list.push(n)
    layers.set(n.layer, list)
  }
  const pos: Record<string, { x: number; y: number }> = {}
  for (const [layer, list] of layers) {
    list.forEach((n, i) => {
      pos[n.id] = { x: layer * 260, y: (i - (list.length - 1) / 2) * 90 }
    })
  }
  return pos
}

export const FlowGraph = forwardRef<
  FlowGraphHandle,
  {
    nodes: FlowNode[]
    edges: FlowEdge[]
    layout: 'layers' | 'force' | 'circle'
    selected?: string | null
    onSelect?: (id: string | null) => void
    onExpand?: (id: string) => void
    height?: number
  }
>(function FlowGraph({ nodes, edges, layout, selected, onSelect, onExpand, height = 560 }, ref) {
  const container = useRef<HTMLDivElement>(null)
  const cy = useRef<Core | null>(null)
  const handlers = useRef({ onSelect, onExpand })
  handlers.current = { onSelect, onExpand }

  useImperativeHandle(ref, () => ({
    png: () => cy.current?.png({ full: true, scale: 2, bg: cssVar('--surface') }) ?? null,
    fit: () => cy.current?.fit(undefined, 40),
  }))

  // Build / rebuild when the data or layout changes.
  useEffect(() => {
    if (!container.current) return
    const style = (): cytoscape.StylesheetJson => {
      const text = cssVar('--text')
      const muted = cssVar('--muted')
      const surface = cssVar('--surface')
      const line = cssVar('--border')
      const accent = cssVar('--accent')
      return [
        {
          selector: 'node',
          style: {
            'background-color': 'data(color)',
            width: 'data(size)',
            height: 'data(size)',
            label: 'data(label)',
            'font-size': 11,
            'font-family': 'Vazirmatn, system-ui, sans-serif',
            color: text,
            'text-valign': 'bottom',
            'text-margin-y': 6,
            'text-wrap': 'wrap',
            'text-max-width': '160px',
            'text-background-color': surface,
            'text-background-opacity': 0.85,
            'text-background-padding': '2px',
            'border-width': 2,
            'border-color': surface,
          },
        },
        { selector: 'node[?dim]', style: { opacity: 0.75, 'border-style': 'dashed', 'border-color': muted } },
        { selector: 'node:selected', style: { 'border-color': accent, 'border-width': 4 } },
        {
          selector: 'edge',
          style: {
            width: 'data(width)',
            'line-color': line,
            'target-arrow-color': line,
            'target-arrow-shape': 'triangle',
            'arrow-scale': 0.9,
            'curve-style': 'bezier',
            label: 'data(label)',
            'font-size': 10,
            color: muted,
            'text-rotation': 'autorotate',
            'text-background-color': surface,
            'text-background-opacity': 0.9,
            'text-background-padding': '1px',
          },
        },
        { selector: 'edge[tone = "strong"]', style: { 'line-color': accent, 'target-arrow-color': accent } },
        { selector: 'edge[tone = "weak"]', style: { 'line-style': 'dashed' } },
        { selector: 'edge:selected', style: { 'line-color': accent, 'target-arrow-color': accent } },
      ]
    }

    const elements: ElementDefinition[] = [
      ...nodes.map((n) => ({
        data: {
          id: n.id,
          label: n.sub ? `${n.text}\n${n.sub}` : n.text,
          color: cssVar(KIND_VAR[n.kind]),
          size: n.kind === 'root' ? 38 : 26,
          dim: n.dim ? 1 : 0,
        },
      })),
      ...edges.map((e) => ({
        data: {
          id: e.id,
          source: e.source,
          target: e.target,
          label: e.text,
          width: 1.5 + e.weight * 6,
          tone: e.tone ?? 'normal',
        },
      })),
    ]

    const positions = layerPositions(nodes)
    const instance = cytoscape({
      container: container.current,
      elements,
      style: style(),
      layout:
        layout === 'layers'
          ? { name: 'preset', positions, fit: true, padding: 40 }
          : layout === 'circle'
            ? { name: 'circle', padding: 50, spacingFactor: 1.1, avoidOverlap: true }
            : { name: 'cose', animate: false, padding: 40, nodeRepulsion: () => 9000, idealEdgeLength: () => 140 },
      wheelSensitivity: 0.3,
      minZoom: 0.1,
      maxZoom: 3,
    })
    instance.on('tap', 'node', (e) => handlers.current.onSelect?.(e.target.id()))
    instance.on('tap', (e) => {
      if (e.target === instance) handlers.current.onSelect?.(null)
    })
    instance.on('dbltap', 'node', (e) => handlers.current.onExpand?.(e.target.id()))
    cy.current = instance

    // Recolor when the theme (class on <html>) changes.
    const observer = new MutationObserver(() => {
      instance.style(style())
      instance.nodes().forEach((n) => {
        const kind = nodes.find((x) => x.id === n.id())?.kind ?? 'plain'
        n.data('color', cssVar(KIND_VAR[kind]))
      })
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })

    return () => {
      observer.disconnect()
      instance.destroy()
      cy.current = null
    }
  }, [nodes, edges, layout])

  useEffect(() => {
    const instance = cy.current
    if (!instance) return
    instance.$(':selected').unselect()
    if (selected) instance.getElementById(selected).select()
  }, [selected, nodes])

  return <div ref={container} dir="ltr" className="w-full rounded-lg bg-surface-2" style={{ height }} />
})

export function Legend({ items }: { items: { kind: NodeKind; text: string }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {items.map((i) => (
        <span key={i.kind} className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-full" style={{ background: `var(${KIND_VAR[i.kind]})` }} />
          {i.text}
        </span>
      ))}
    </div>
  )
}
