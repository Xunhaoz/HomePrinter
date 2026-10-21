import { useEffect, useState } from "react"

// Tiny hash router: #/print?mode=cron -> { path: "/print", params }.
// Hash routes need no server rewrites and survive "Add to Home Screen".

export type Route = { path: string; params: URLSearchParams }

function parse(): Route {
  const [path, query] = (window.location.hash.slice(1) || "/").split("?")
  return { path: path || "/", params: new URLSearchParams(query) }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(parse)
  useEffect(() => {
    const onChange = () => {
      setRoute(parse())
      window.scrollTo({ top: 0 })
    }
    window.addEventListener("hashchange", onChange)
    return () => window.removeEventListener("hashchange", onChange)
  }, [])
  return route
}

export function navigate(to: string) {
  window.location.hash = to
}
