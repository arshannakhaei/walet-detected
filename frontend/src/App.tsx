import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { CaseDetail } from './pages/CaseDetail'
import { Cases } from './pages/Cases'
import { Home } from './pages/Home'
import { Investigation } from './pages/Investigation'
import { Labels } from './pages/Labels'
import { Links } from './pages/Links'
import { Settings } from './pages/Settings'
import { Trace } from './pages/Trace'
import { Watchlist } from './pages/Watchlist'
import { WalletPage } from './pages/wallet/WalletPage'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/wallet/:chain/:address" element={<WalletPage />} />
        <Route path="/trace" element={<Trace />} />
        <Route path="/links" element={<Links />} />
        <Route path="/investigation" element={<Investigation />} />
        <Route path="/cases" element={<Cases />} />
        <Route path="/cases/:id" element={<CaseDetail />} />
        <Route path="/watchlist" element={<Watchlist />} />
        <Route path="/labels" element={<Labels />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}
