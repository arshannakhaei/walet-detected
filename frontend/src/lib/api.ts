// Typed client for the ChainTrace REST API. Decimal amounts arrive as strings.

export type Chain =
  | 'tron'
  | 'ethereum'
  | 'bsc'
  | 'polygon'
  | 'arbitrum'
  | 'optimism'
  | 'base'
  | 'avalanche'
  | 'bitcoin'
  | 'solana'

export type Direction = 'in' | 'out' | 'self'
export type LabelCategory =
  | 'exchange'
  | 'bridge'
  | 'mixer'
  | 'defi'
  | 'token_contract'
  | 'sanctioned'
  | 'scam'
  | 'service'
  | 'personal'
  | 'other'

export interface Label {
  chain: Chain
  address: string
  name: string
  category: LabelCategory
  note: string | null
  source: 'builtin' | 'user'
}

export interface Transfer {
  chain: Chain
  transfer_id: string
  tx_hash: string
  timestamp: string
  from_address: string
  to_address: string
  amount: string
  token_symbol: string
  token_contract: string | null
  token_decimals: number
  success: boolean
}

export interface TransferView {
  transfer: Transfer
  direction: Direction
  counterparty: string
}

export interface TokenBalance {
  token_symbol: string
  token_contract: string | null
  amount: string
  usd_value: string | null
  derived: boolean
}

export interface TokenFlow {
  token_symbol: string
  token_contract: string | null
  total_in: string
  total_out: string
  count_in: number
  count_out: number
  usd_in: string | null
  usd_out: string | null
}

export interface WalletOverview {
  chain: Chain
  address: string
  balances: TokenBalance[]
  total_usd: string | null
  first_seen: string | null
  last_seen: string | null
  transfer_count: number
  counterparty_count: number
  flows: TokenFlow[]
  truncated: boolean
  loading_more: boolean
}

export interface Counterparty {
  address: string
  token_symbol: string
  token_contract: string | null
  received_from: string
  sent_to: string
  count_in: number
  count_out: number
  first_seen: string
  last_seen: string
}

export interface GraphNode {
  address: string
  depth: number
  label: Label | null
  is_hub: boolean
  expanded: boolean
  stop_reason: string | null
}

export interface GraphEdge {
  from_address: string
  to_address: string
  token_symbol: string
  token_contract: string | null
  amount: string
  count: number
  first_seen: string
  last_seen: string
  tx_hashes: string[]
}

export interface Graph {
  chain: Chain
  root: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  truncated: boolean
}

export type EndReason =
  | 'unspent'
  | 'unknown_source'
  | 'labeled'
  | 'hub'
  | 'max_hops'
  | 'below_min'
  | 'pruned'
  | 'step_limit'
  | 'loop'

export interface TraceFlow {
  hop: number
  transfer_id: string
  tx_hash: string
  from_address: string
  to_address: string
  timestamp: string
  transfer_amount: string
  traced_amount: string
  confidence: string
  match: string
}

export interface TraceEndpoint {
  address: string
  amount: string
  reason: EndReason
  confidence: string
  label: Label | null
}

export interface TraceResult {
  chain: Chain
  direction: 'forward' | 'backward'
  method: 'fifo' | 'lifo'
  token_symbol: string
  token_contract: string | null
  start: Transfer
  traced_amount: string
  nodes: { address: string; label: Label | null; is_hub: boolean }[]
  flows: TraceFlow[]
  endpoints: TraceEndpoint[]
  summary: Partial<Record<EndReason, string>>
}

export interface TraceRequest {
  address: string
  tx_hash: string
  chain?: Chain | null
  token?: string | null
  amount?: string | null
  direction: 'forward' | 'backward'
  method: 'fifo' | 'lifo'
  max_hops: number
  min_amount: string
  max_branches: number
  tolerance: string
  exact_window_hours: number
}

export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical'

export interface Finding {
  code: string
  severity: Severity
  points: number
  title: string
  detail: string
  evidence: string[]
}

export interface RiskReport {
  chain: Chain
  address: string
  score: number
  level: 'low' | 'medium' | 'high' | 'critical'
  findings: Finding[]
  stats: {
    main_token: string | null
    lifetime_days: number | null
    active_days: number
    median_holding_hours: number | null
    pass_through_ratio: number | null
    distinct_senders: number
    distinct_receivers: number
  }
  truncated: boolean
}

export interface TimelineRow {
  period: string
  token_symbol: string
  token_contract: string | null
  amount_in: string
  amount_out: string
  count_in: number
  count_out: number
}

export interface ChainInfo {
  chain: Chain
  supported: boolean
}

export interface CaseSummary {
  id: number
  title: string
  description: string
  status: 'open' | 'closed'
  created_at: string
  updated_at: string
  item_count: number
}

export interface CaseItem {
  id: number
  kind: 'address' | 'trace' | 'note'
  chain: Chain | null
  address: string | null
  title: string
  note: string
  data: TraceResult | null
  created_at: string
}

export interface Case extends CaseSummary {
  items: CaseItem[]
}

export interface Watch {
  id: number
  chain: Chain
  address: string
  name: string
  min_amount: string
  token: string | null
  telegram_chat_id: string | null
  last_checked_at: string | null
  created_at: string
}

export interface Alert {
  id: number
  watch_id: number
  watch_name: string
  chain: Chain
  address: string
  tx_hash: string
  direction: Direction
  counterparty: string
  amount: string
  token_symbol: string
  timestamp: string
  read: boolean
}

export interface TransferFilters {
  token?: string
  direction?: 'in' | 'out' | ''
  min_amount?: string
  max_amount?: string
  start?: string
  end?: string
  counterparty?: string
  hide_spam?: boolean
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Params = Record<string, string | number | boolean | null | undefined>

export function query(params: Params): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok) {
    let message = resp.statusText
    try {
      const data = await resp.json()
      message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data)
    } catch {
      /* not JSON */
    }
    throw new ApiError(resp.status, message)
  }
  if (resp.status === 204) return undefined as T
  return resp.json() as Promise<T>
}

const get = <T,>(path: string) => request<T>('GET', path)
const enc = encodeURIComponent

export function walletPath(address: string, suffix: string, chain?: Chain, params: Params = {}): string {
  return `/api/wallet/${enc(address)}/${suffix}${query({ chain, ...params })}`
}

export interface Health {
  status: string
  chains: Chain[]
  demo: { scammer: string; victim: string; mule: string } | null
}

export interface KeyStatus {
  trongrid_api_key: boolean
  etherscan_api_key: boolean
  coingecko_api_key: boolean
  solana_rpc_url: string
  usd_toman_rate: string | null
  demo_mode: boolean
  tron_requests_per_second: number
  evm_requests_per_second: number
  can_edit: boolean
}

export interface Quote {
  usd_then: string | null
  usd_now: string | null
  toman_rate_then: string | null
  toman_rate_now: string | null
}

export interface TomanRate {
  rate: string | null
  source: 'manual' | 'nobitex' | 'wallex' | null
  updated_at: string | null
}

export interface KeysIn {
  trongrid_api_key?: string
  etherscan_api_key?: string
  coingecko_api_key?: string
  solana_rpc_url?: string
  usd_toman_rate?: string
}

export interface LinkTransfer {
  tx_hash: string
  timestamp: string
  from_address: string
  to_address: string
  amount: string
  token_symbol: string
}

export interface DirectLink {
  from_address: string
  to_address: string
  token_symbol: string
  token_contract: string | null
  total: string
  count: number
  first_seen: string
  last_seen: string
  transfers: LinkTransfer[]
}

export interface MatchedHop {
  incoming: LinkTransfer
  outgoing: LinkTransfer
  delay_minutes: number
}

export interface PathLink {
  from_address: string
  to_address: string
  via: string[]
  via_labels: (Label | null)[]
  token_symbol: string
  token_contract: string | null
  amount_in: string
  amount_out: string
  matched: MatchedHop[]
  matched_amount: string
  through_service: boolean
}

export interface SharedCounterparty {
  address: string
  label: Label | null
  role: 'common_source' | 'common_destination'
  token_symbol: string
  token_contract: string | null
  members: { address: string; amount: string; count: number }[]
  total: string
}

export interface LinkMember {
  address: string
  index: number
  label: Label | null
  transfer_count: number
  truncated: boolean
  error: string | null
  sent_to_members: string
  received_from_members: string
  linked_members: number
  group: number | null
}

export interface LinkReport {
  chain: Chain
  token: string | null
  members: LinkMember[]
  direct: DirectLink[]
  paths: PathLink[]
  shared: SharedCounterparty[]
  groups: string[][]
  intermediaries_checked: number
  complete: boolean
}

export interface LinkJob {
  id: string
  state: 'running' | 'done' | 'failed'
  stage: 'members' | 'intermediaries'
  done: number
  total: number
  error: string | null
  result?: LinkReport | null
}

export interface LinksRequest {
  addresses: string
  chain?: Chain | null
  token: string | null
  min_amount: string
  deep: boolean
  match_window_hours: number
}

export const isRateLimit = (e: unknown) => e instanceof Error && /rate limit|HTTP 429/i.test(e.message)

export const api = {
  settings: () => get<KeyStatus>('/api/settings'),
  saveKeys: (body: KeysIn) => request<KeyStatus>('PUT', '/api/settings/keys', body),
  health: () => get<Health>('/api/health'),
  rates: () => get<TomanRate>('/api/prices/rates'),
  quotes: (items: { chain: Chain; symbol: string; contract: string | null; day: string | null }[]) =>
    request<Quote[]>('POST', '/api/prices/quotes', { items }),
  chains: () => get<ChainInfo[]>('/api/chains'),
  detect: (address: string) => get<{ address: string; chains: ChainInfo[] }>(`/api/detect/${enc(address)}`),
  activity: (address: string) =>
    get<{ chain: Chain; active: boolean | null; error: string | null }[]>(`/api/wallet/${enc(address)}/activity`),

  overview: (address: string, chain: Chain) => get<WalletOverview>(walletPath(address, 'overview', chain)),
  refresh: (address: string, chain: Chain) =>
    request<WalletOverview>('POST', walletPath(address, 'refresh', chain)),
  transfers: (address: string, chain: Chain, f: TransferFilters, limit: number, offset: number) =>
    get<{ total: number; items: TransferView[] }>(
      walletPath(address, 'transfers', chain, { ...f, limit, offset }),
    ),
  counterparties: (address: string, chain: Chain, f: TransferFilters) =>
    get<Counterparty[]>(walletPath(address, 'counterparties', chain, { ...f, limit: 1000 })),
  risk: (address: string, chain: Chain, deep: boolean) =>
    get<RiskReport>(walletPath(address, 'risk', chain, { deep })),
  timeline: (address: string, chain: Chain, bucket: string, token?: string) =>
    get<TimelineRow[]>(walletPath(address, 'timeline', chain, { bucket, token })),

  graph: (
    address: string,
    chain: Chain,
    p: { depth_in: number; depth_out: number; max_nodes: number; max_children: number; follow_time: boolean } & TransferFilters,
  ) => get<Graph>(`/api/graph/${enc(address)}${query({ chain, ...p })}`),
  trace: (body: TraceRequest) => request<TraceResult>('POST', '/api/trace', body),
  startLinks: (body: LinksRequest) => request<LinkJob>('POST', '/api/links', body),
  linkJob: (id: string) => get<LinkJob>(`/api/links/${enc(id)}`),

  labels: (chain?: Chain) => get<Label[]>(`/api/labels${query({ chain })}`),
  setLabel: (chain: Chain, address: string, body: { name: string; category: LabelCategory; note?: string | null }) =>
    request<Label>('PUT', `/api/labels/${chain}/${enc(address)}`, body),
  deleteLabel: (chain: Chain, address: string) => request<void>('DELETE', `/api/labels/${chain}/${enc(address)}`),

  cases: () => get<CaseSummary[]>('/api/cases'),
  case: (id: number) => get<Case>(`/api/cases/${id}`),
  createCase: (title: string, description = '') => request<Case>('POST', '/api/cases', { title, description }),
  updateCase: (id: number, patch: Partial<Pick<Case, 'title' | 'description' | 'status'>>) =>
    request<Case>('PATCH', `/api/cases/${id}`, patch),
  deleteCase: (id: number) => request<void>('DELETE', `/api/cases/${id}`),
  addCaseItem: (
    id: number,
    item: {
      kind: CaseItem['kind']
      chain?: Chain | null
      address?: string | null
      title?: string
      note?: string
      data?: TraceResult
    },
  ) => request<CaseItem>('POST', `/api/cases/${id}/items`, item),
  deleteCaseItem: (id: number, itemId: number) => request<void>('DELETE', `/api/cases/${id}/items/${itemId}`),

  watchlist: () => get<Watch[]>('/api/watchlist'),
  addWatch: (body: { address: string; chain?: Chain | null; name?: string; min_amount?: string; token?: string | null }) =>
    request<Watch>('POST', '/api/watchlist', body),
  removeWatch: (id: number) => request<void>('DELETE', `/api/watchlist/${id}`),
  checkWatchlist: () => request<Alert[]>('POST', '/api/watchlist/check'),
  alerts: (unreadOnly = false) => get<Alert[]>(`/api/alerts${query({ unread_only: unreadOnly })}`),
  markAlertsRead: (ids?: number[]) => request<void>('POST', '/api/alerts/read', { ids: ids ?? null }),
}
