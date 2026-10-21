import { useState } from "react"
import { FileTextIcon, ImageIcon, Loader2Icon, XIcon } from "lucide-react"
import { toast } from "sonner"
import { api, type Job } from "@/lib/api"
import { JOB_STATUS, errorMessage, formatWhen, isActive } from "@/lib/format"
import { StatusBadge } from "@/components/common"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"

function jobTone(job: Job) {
  switch (job.status) {
    case "completed": return "ok" as const
    case "failed": return "error" as const
    case "canceled": return "muted" as const
    case "queued": return "muted" as const
    default: return "busy" as const
  }
}

export function JobCard({ job, onChange }: { job: Job; onChange?: () => void }) {
  const [canceling, setCanceling] = useState(false)
  const active = isActive(job.status)
  const percent = job.pages_total ? Math.round((job.pages_done / job.pages_total) * 100) : 0
  const Icon = job.kind === "pdf" ? FileTextIcon : ImageIcon

  async function cancel() {
    setCanceling(true)
    try {
      await api.cancelJob(job.id)
      onChange?.()
    } catch (err) {
      toast.error("取消失敗", { description: errorMessage(err) })
      setCanceling(false)
    }
  }

  const detail = [
    job.copies > 1 && `${job.copies} 份`,
    job.sides !== "one-sided" && "雙面",
    job.pages && job.pages.length > 0 && `${job.pages.length} 頁`,
    formatWhen(job.created_at),
  ].filter(Boolean).join(" · ")

  return (
    <div className="rounded-xl border bg-card p-3.5">
      <div className="flex items-start gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <Icon className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate font-medium">{job.filename}</p>
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>
        </div>
        <StatusBadge tone={jobTone(job)} pulse={active}>
          {job.dry_run && job.status === "completed" ? "試印完成" : canceling && active ? "取消中…" : JOB_STATUS[job.status]}
        </StatusBadge>
      </div>

      {active && (
        <div className="mt-3 flex items-center gap-3">
          {job.status === "printing" && job.pages_total > 0 ? (
            <>
              <Progress value={percent} className="h-1.5 flex-1" />
              <span className="text-xs tabular-nums text-muted-foreground">{job.pages_done}/{job.pages_total}</span>
            </>
          ) : (
            <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
              <div className="absolute inset-y-0 w-1/3 animate-[indeterminate_1.4s_ease-in-out_infinite] rounded-full bg-primary/60" />
            </div>
          )}
          <Button variant="ghost" size="sm" className="h-9 text-muted-foreground" onClick={cancel} disabled={canceling}>
            {canceling ? <Loader2Icon className="animate-spin" /> : <XIcon />}
            取消
          </Button>
        </div>
      )}
      {job.status === "completed" && !job.dry_run && (
        <p className="mt-2 text-xs text-muted-foreground">已完成，請到印表機取件</p>
      )}
      {job.error && <p className="mt-2 text-sm text-destructive">{errorMessage(job.error)}</p>}
    </div>
  )
}
