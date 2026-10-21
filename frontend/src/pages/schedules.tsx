import { useState } from "react"
import { CalendarClockIcon, DropletsIcon, MoreHorizontalIcon, PlayIcon, PlusIcon, Trash2Icon } from "lucide-react"
import { toast } from "sonner"
import { api, type Schedule } from "@/lib/api"
import { describeSchedule, errorMessage, formatWhen, mediaName } from "@/lib/format"
import { usePoll } from "@/hooks/use-poll"
import { EmptyState, PageHeader, StatusBadge } from "@/components/common"
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"

const LAST_STATUS = {
  submitted: { tone: "ok", text: "已送出列印" },
  failed: { tone: "error", text: "失敗" },
  missed: { tone: "warn", text: "已錯過" },
} as const

export function SchedulesPage() {
  const schedules = usePoll(api.schedules, 60000)
  const list = schedules.data ?? []
  const hasNozzle = list.some((s) => s.filename === "nozzle-check.pdf")

  return (
    <div className="space-y-6">
      <PageHeader
        title="排程"
        description="在指定時間自動列印"
        action={
          <Button asChild className="h-10">
            <a href="#/print?mode=once">
              <PlusIcon />
              新增
            </a>
          </Button>
        }
      />

      {!hasNozzle && !schedules.loading && (
        <a
          href="#/print?template=nozzle"
          className="flex items-center gap-3 rounded-xl border bg-card p-4 transition-colors hover:bg-muted/50 active:bg-muted"
        >
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-sky-500/12 text-sky-600 dark:text-sky-400">
            <DropletsIcon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="font-medium">每週噴頭保養</p>
            <p className="text-sm text-muted-foreground">每週六 10:00 印一張彩色測試頁，避免墨水乾掉堵塞噴頭</p>
          </div>
          <PlusIcon className="size-5 text-muted-foreground" />
        </a>
      )}

      {schedules.loading && !schedules.data ? (
        <div className="space-y-3">
          <Skeleton className="h-28 w-full rounded-xl" />
          <Skeleton className="h-28 w-full rounded-xl" />
        </div>
      ) : list.length > 0 ? (
        <div className="space-y-3">
          {list.map((s) => <ScheduleCard key={s.id} schedule={s} onChange={schedules.refresh} />)}
        </div>
      ) : (
        <EmptyState
          icon={<CalendarClockIcon />}
          title="還沒有排程"
          description="可以設定明天早上印出通知單，或每週定期列印。"
          action={<Button asChild variant="outline" className="h-10"><a href="#/print?mode=once">新增排程</a></Button>}
        />
      )}
    </div>
  )
}

function ScheduleCard({ schedule: s, onChange }: { schedule: Schedule; onChange: () => void }) {
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const oneShotDone = s.kind === "once" && !s.enabled && !!s.last_run_at

  async function act(action: () => Promise<unknown>, success?: string) {
    setBusy(true)
    try {
      await action()
      if (success) toast.success(success)
      onChange()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const options = [
    s.options.media && mediaName(s.options.media),
    s.options.copies > 1 && `${s.options.copies} 份`,
    s.options.color_mode === "monochrome" ? "黑白" : s.options.color_mode === "color" ? "彩色" : null,
    s.options.sides !== "one-sided" && "雙面",
    s.options.dry_run && "試印",
  ].filter(Boolean).join(" · ")

  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{s.name || s.filename}</p>
          <p className="mt-0.5 text-sm">{describeSchedule(s)}</p>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {s.name ? `${s.filename}${options ? " · " : ""}` : ""}{options}
          </p>
        </div>
        {!oneShotDone && (
          <Switch
            checked={s.enabled}
            disabled={busy}
            aria-label={s.enabled ? "暫停排程" : "啟用排程"}
            onCheckedChange={(enabled) => act(() => api.updateSchedule(s.id, { enabled }), enabled ? "已啟用" : "已暫停")}
          />
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-lg" aria-label="更多動作" className="-my-1 -mr-2">
              <MoreHorizontalIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => act(() => api.runSchedule(s.id), "已送出列印")}>
              <PlayIcon />
              立即列印一次
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={() => setConfirmDelete(true)}>
              <Trash2Icon />
              刪除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t pt-3 text-xs text-muted-foreground">
        {s.enabled && s.next_run_at ? (
          <span>下次：<span className="text-foreground">{formatWhen(s.next_run_at)}</span></span>
        ) : (
          <span>{oneShotDone ? "已執行完畢" : "已暫停"}</span>
        )}
        {s.last_status && (
          <span className="flex items-center gap-1.5">
            上次 {formatWhen(s.last_run_at)}
            <StatusBadge tone={LAST_STATUS[s.last_status].tone}>{LAST_STATUS[s.last_status].text}</StatusBadge>
          </span>
        )}
      </div>
      {s.last_status === "failed" && s.last_error && (
        <p className="mt-2 text-xs text-destructive">{errorMessage(s.last_error)}</p>
      )}

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>刪除這個排程？</AlertDialogTitle>
            <AlertDialogDescription>排程和它保存的檔案「{s.filename}」都會刪除。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => act(() => api.deleteSchedule(s.id), "已刪除")}>刪除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
