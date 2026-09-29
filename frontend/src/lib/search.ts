import { api } from './api'
import { readJson, writeJson } from './storage'

export interface Recent {
  chain: string
  address: string
  at: number
}

const KEY = 'ct-recent'

export function recentSearches(): Recent[] {
  return readJson<Recent[]>(KEY, [])
}

export function rememberSearch(chain: string, address: string): void {
  const list = recentSearches().filter((r) => !(r.chain === chain && r.address === address))
  writeJson(KEY, [{ chain, address, at: Date.now() }, ...list].slice(0, 12))
}

export function clearRecent(): void {
  writeJson(KEY, [])
}

/** Route for a typed address, or null when the format is not recognized. */
export async function resolveSearch(raw: string): Promise<string | null> {
  const address = raw.trim()
  if (!address) return null
  try {
    const { chains } = await api.detect(address)
    const pick = chains.find((c) => c.supported) ?? chains[0]
    return pick ? `/wallet/${pick.chain}/${address}` : null
  } catch {
    return null
  }
}
