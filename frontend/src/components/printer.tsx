import { AlertTriangleIcon, PrinterIcon, WifiOffIcon } from "lucide-react"
import type { PrinterStatus } from "@/lib/api"
import { PRINTER_STATE, inkName, mediaName, stateReasons } from "@/lib/format"
import { StatusBadge } from "@/components/common"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

const STATE_TONE = { idle: "ok", processing: "busy", stopped: "error", unknown: "muted" } as const

export function PrinterCard({ status, error, loading }: { status?: PrinterStatus; error?: unknown; loading: boolean }) {
  if (loading && !status) {
    return (
      <Card>
        <CardContent className="space-y-4">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    )
  }
  if (!status) {
    return (
      <Alert variant="destructive">
        <WifiOffIcon />
        <AlertTitle>連不到印表機</AlertTitle>
        <AlertDescription>請確認印表機有開機、連上 Wi-Fi。送出的列印會先排隊，連上後自動開始。</AlertDescription>
      </Alert>
    )
  }

  const reasons = stateReasons(status.state_reasons)
  const media = Array.isArray(status.media_ready) ? status.media_ready[0] : status.media_ready

  return (
    <Card>
      <CardContent className="space-y-5">
        <div className="flex items-center gap-3">
          <div className="flex size-11 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <PrinterIcon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">{status.model ?? "印表機"}</p>
            <p className="text-xs text-muted-foreground">
              {media ? `紙匣：${mediaName(media)}` : "紙匣：未知"}
              {error ? " · 狀態可能不是最新" : ""}
            </p>
          </div>
          <StatusBadge tone={STATE_TONE[status.state]} pulse={status.state === "processing"}>
            {PRINTER_STATE[status.state]}
          </StatusBadge>
        </div>

        {reasons.length > 0 && (
          <div className="space-y-2">
            {reasons.map((r) => (
              <div
                key={r.text}
                className={
                  r.severe
                    ? "flex items-center gap-2 rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive"
                    : "flex items-center gap-2 rounded-lg bg-amber-500/12 px-3 py-2 text-sm text-amber-700 dark:text-amber-400"
                }
              >
                <AlertTriangleIcon className="size-4 shrink-0" />
                {r.text}
              </div>
            ))}
          </div>
        )}

        {status.ink.length > 0 && <InkLevels ink={status.ink} />}
      </CardContent>
    </Card>
  )
}

function InkLevels({ ink }: { ink: PrinterStatus["ink"] }) {
  return (
    <div className="grid grid-cols-4 gap-3">
      {ink.map((i) => {
        const known = i.level >= 0
        const low = known && i.level <= 15
        return (
          <div key={i.name} className="flex flex-col items-center gap-2">
            <div className="relative h-20 w-full max-w-12 overflow-hidden rounded-lg bg-muted ring-1 ring-foreground/5">
              <div
                className="absolute inset-x-0 bottom-0 transition-[height] duration-500"
                style={{ height: `${known ? Math.max(i.level, 2) : 0}%`, background: i.color || "var(--foreground)" }}
              />
            </div>
            <div className="text-center leading-tight">
              <p className="text-xs font-medium">{inkName(i.name)}</p>
              <p className={low ? "text-xs font-medium text-destructive" : "text-xs text-muted-foreground tabular-nums"}>
                {known ? `${i.level}%` : "—"}
              </p>
            </div>
          </div>
        )
      })}
    </div>
  )
}
