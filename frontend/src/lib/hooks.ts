import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { api, type Chain, type Label } from './api'

/** All labels of a chain as an address -> label map (built-in lists + user labels). */
export function useLabelMap(chain: Chain | undefined): Map<string, Label> {
  const { data } = useQuery({
    queryKey: ['labels', chain],
    queryFn: () => api.labels(chain),
    enabled: !!chain,
    staleTime: 5 * 60_000,
  })
  return useMemo(() => new Map((data ?? []).map((l) => [l.address, l])), [data])
}

export function useChains() {
  return useQuery({ queryKey: ['chains'], queryFn: api.chains, staleTime: 10 * 60_000 })
}
