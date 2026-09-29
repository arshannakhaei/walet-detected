import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

function current(): Theme {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(current)
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])
  const setTheme = useCallback((t: Theme) => {
    setThemeState(t)
    try {
      localStorage.setItem('ct-theme', t)
    } catch {
      /* storage unavailable */
    }
  }, [])
  return [theme, setTheme]
}

/** Reads a CSS custom property (for canvas-based charts that can't use var()). */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}
