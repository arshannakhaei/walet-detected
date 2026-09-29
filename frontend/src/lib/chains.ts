import type { Chain } from './api'

interface ChainMeta {
  name: string
  short: string
  color: string
  address: (a: string) => string
  tx: (h: string) => string
}

const evm = (host: string) => ({
  address: (a: string) => `https://${host}/address/${a}`,
  tx: (h: string) => `https://${host}/tx/${h}`,
})

export const CHAINS: Record<Chain, ChainMeta> = {
  tron: {
    name: 'Tron',
    short: 'TRX',
    color: '#e5484d',
    address: (a) => `https://tronscan.org/#/address/${a}`,
    tx: (h) => `https://tronscan.org/#/transaction/${h}`,
  },
  ethereum: { name: 'Ethereum', short: 'ETH', color: '#627eea', ...evm('etherscan.io') },
  bsc: { name: 'BNB Chain', short: 'BSC', color: '#d4a106', ...evm('bscscan.com') },
  polygon: { name: 'Polygon', short: 'POL', color: '#8247e5', ...evm('polygonscan.com') },
  arbitrum: { name: 'Arbitrum', short: 'ARB', color: '#2d74da', ...evm('arbiscan.io') },
  optimism: { name: 'Optimism', short: 'OP', color: '#ff0420', ...evm('optimistic.etherscan.io') },
  base: { name: 'Base', short: 'BASE', color: '#0052ff', ...evm('basescan.org') },
  avalanche: { name: 'Avalanche', short: 'AVAX', color: '#e84142', ...evm('snowtrace.io') },
  bitcoin: {
    name: 'Bitcoin',
    short: 'BTC',
    color: '#f7931a',
    address: (a) => `https://mempool.space/address/${a}`,
    tx: (h) => `https://mempool.space/tx/${h}`,
  },
  solana: {
    name: 'Solana',
    short: 'SOL',
    color: '#14b89c',
    address: (a) => `https://solscan.io/account/${a}`,
    tx: (h) => `https://solscan.io/tx/${h}`,
  },
}

export const ALL_CHAINS = Object.keys(CHAINS) as Chain[]
