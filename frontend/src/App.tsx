import { useCallback, useEffect, useState } from "react"
import { CalendarClockIcon, HouseIcon, Loader2Icon, LogOutIcon, PrinterIcon, ScanLineIcon } from "lucide-react"
import { api, UNAUTHORIZED_EVENT, type Session } from "@/lib/api"
import { cn } from "@/lib/utils"
import { useRoute } from "@/hooks/use-route"
import { HomePage } from "@/pages/home"
import { LoginPage } from "@/pages/login"
import { PrintPage } from "@/pages/print"
import { ScanPage } from "@/pages/scan"
import { SchedulesPage } from "@/pages/schedules"
import { Button } from "@/components/ui/button"
import { Toaster } from "@/components/ui/sonner"

const TABS = [
  { path: "/", label: "首頁", icon: HouseIcon },
  { path: "/print", label: "列印", icon: PrinterIcon },
  { path: "/scan", label: "掃描", icon: ScanLineIcon },
  { path: "/schedules", label: "排程", icon: CalendarClockIcon },
]

export default function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [offline, setOffline] = useState(false)

  const check = useCallback(() => {
    api.session()
      .then((s) => {
        setSession(s)
        setOffline(false)
      })
      .catch(() => setOffline(true))
  }, [])

  useEffect(() => {
    check()
    const onUnauthorized = () => setSession((s) => (s ? { ...s, authenticated: false } : s))
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [check])

  let content
  if (!session) {
    content = (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
        {offline ? (
          <>
            <p className="font-medium">連不到 HomePrinter 伺服器</p>
            <Button variant="outline" className="h-11" onClick={check}>重試</Button>
          </>
        ) : (
          <Loader2Icon className="size-6 animate-spin text-muted-foreground" />
        )}
      </div>
    )
  } else if (session.auth_required && !session.authenticated) {
    content = <LoginPage onLogin={check} />
  } else {
    content = (
      <Shell
        showLogout={session.auth_required}
        onLogout={async () => {
          await api.logout().catch(() => undefined)
          check()
        }}
      />
    )
  }

  return (
    <>
      {content}
      <Toaster position="top-center" />
    </>
  )
}

function Shell({ showLogout, onLogout }: { showLogout: boolean; onLogout: () => void }) {
  const route = useRoute()
  const page = (() => {
    switch (route.path) {
      case "/print": return <PrintPage key={route.params.toString()} route={route} />
      case "/scan": return <ScanPage />
      case "/schedules": return <SchedulesPage />
      default: return <HomePage />
    }
  })()

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_minmax(0,1fr)]">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-dvh flex-col border-r bg-sidebar p-4 lg:flex">
        <div className="flex items-center gap-2.5 px-2 py-1.5">
          <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <PrinterIcon className="size-4" />
          </div>
          <span className="font-semibold">HomePrinter</span>
        </div>
        <nav className="mt-6 flex flex-col gap-1">
          {TABS.map(({ path, label, icon: Icon }) => (
            <a
              key={path}
              href={`#${path}`}
              aria-current={route.path === path ? "page" : undefined}
              className={cn(
                "flex h-10 items-center gap-3 rounded-lg px-3 text-sm transition-colors hover:bg-sidebar-accent",
                route.path === path && "bg-sidebar-accent font-medium",
              )}
            >
              <Icon className="size-4" />
              {label}
            </a>
          ))}
        </nav>
        {showLogout && (
          <Button variant="ghost" className="mt-auto h-10 justify-start gap-3 px-3 text-muted-foreground" onClick={onLogout}>
            <LogOutIcon />
            登出
          </Button>
        )}
      </aside>

      <div className="flex min-h-dvh flex-col">
        {/* Mobile top bar: only the logout action, the page has its own title */}
        {showLogout && (
          <div className="flex justify-end px-2 pt-safe lg:hidden">
            <Button variant="ghost" size="sm" className="mt-1 h-9 text-muted-foreground" onClick={onLogout}>
              <LogOutIcon />
              登出
            </Button>
          </div>
        )}
        <main
          className={cn(
            "mx-auto w-full max-w-2xl flex-1 px-4 pb-[calc(var(--nav-h)+1.5rem)] lg:max-w-4xl lg:px-8 lg:pt-10 lg:pb-12",
            !showLogout && "pt-safe-6",
          )}
        >
          {page}
        </main>
      </div>

      {/* Mobile tab bar */}
      <nav
        className="fixed inset-x-0 bottom-0 z-20 border-t bg-background/85 pb-safe backdrop-blur-xl lg:hidden"
        aria-label="主要導覽"
      >
        <div className="mx-auto grid h-14 max-w-md grid-cols-4">
          {TABS.map(({ path, label, icon: Icon }) => {
            const active = route.path === path
            return (
              <a
                key={path}
                href={`#${path}`}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex flex-col items-center justify-center gap-0.5 text-[11px] transition-colors",
                  active ? "text-foreground" : "text-muted-foreground",
                )}
              >
                <Icon className={cn("size-5", active && "stroke-[2.25]")} />
                {label}
              </a>
            )
          })}
        </div>
      </nav>
    </div>
  )
}
