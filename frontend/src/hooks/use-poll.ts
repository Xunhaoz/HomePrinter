import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"

type Poll<T> = {
  data: T | undefined
  error: unknown
  loading: boolean
  refresh: () => Promise<void>
}

/**
 * Loads `fetcher` now and then every `interval` ms. `interval` can be a
 * function of the latest data, returning null to stop polling (e.g. once a
 * scan has finished); any later refresh() starts it again. Pauses while the
 * tab is hidden and reloads as soon as it is visible again.
 */
export function usePoll<T>(fetcher: () => Promise<T>, interval: number | ((data: T | undefined) => number | null)): Poll<T> {
  const [data, setData] = useState<T>()
  const [error, setError] = useState<unknown>()
  const [loading, setLoading] = useState(true)
  const fetcherRef = useRef(fetcher)
  const intervalRef = useRef(interval)
  const dataRef = useRef<T>(undefined)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const alive = useRef(true)
  useLayoutEffect(() => {
    fetcherRef.current = fetcher
    intervalRef.current = interval
  })

  const refresh = useCallback(async function load(): Promise<void> {
    clearTimeout(timer.current)
    if (document.visibilityState === "visible") {
      try {
        const next = await fetcherRef.current()
        dataRef.current = next
        if (alive.current) {
          setData(next)
          setError(undefined)
        }
      } catch (err) {
        if (alive.current) setError(err)
      } finally {
        if (alive.current) setLoading(false)
      }
    }
    if (!alive.current) return
    const every = typeof intervalRef.current === "function" ? intervalRef.current(dataRef.current) : intervalRef.current
    clearTimeout(timer.current)
    if (every !== null) timer.current = setTimeout(load, every)
  }, [])

  useEffect(() => {
    alive.current = true
    refresh()
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh()
    }
    document.addEventListener("visibilitychange", onVisible)
    return () => {
      alive.current = false
      clearTimeout(timer.current)
      document.removeEventListener("visibilitychange", onVisible)
    }
  }, [refresh])

  return { data, error, loading, refresh }
}
