import type { ReactNode } from "react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

export function PageHeader({ title, description, action }: { title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  )
}

export function Field({ label, htmlFor, hint, children, className }: {
  label: string
  htmlFor?: string
  hint?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn("grid gap-2", className)}>
      <Label htmlFor={htmlFor} className="text-sm text-muted-foreground">{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

// A segmented control that always has exactly one value (a plain ToggleGroup can be emptied).
export function Segmented<T extends string>({ value, onChange, options, className, label }: {
  value: T
  onChange: (value: T) => void
  options: { value: T; label: ReactNode; disabled?: boolean }[]
  className?: string
  label?: string
}) {
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      spacing={0}
      value={value}
      aria-label={label}
      onValueChange={(v) => v && onChange(v as T)}
      className={cn("w-full", className)}
    >
      {options.map((o) => (
        <ToggleGroupItem
          key={o.value}
          value={o.value}
          disabled={o.disabled}
          className="h-11 flex-1 text-sm data-[state=on]:bg-primary data-[state=on]:text-primary-foreground"
        >
          {o.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

type Tone = "ok" | "busy" | "warn" | "error" | "muted"

const TONES: Record<Tone, string> = {
  ok: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-400",
  busy: "bg-sky-500/12 text-sky-700 dark:text-sky-400",
  warn: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  error: "bg-destructive/10 text-destructive",
  muted: "bg-muted text-muted-foreground",
}

export function StatusBadge({ tone, children, pulse }: { tone: Tone; children: ReactNode; pulse?: boolean }) {
  return (
    <Badge variant="secondary" className={cn("gap-1.5 border-0 font-medium", TONES[tone])}>
      <span className={cn("size-1.5 rounded-full bg-current", pulse && "animate-pulse")} />
      {children}
    </Badge>
  )
}

export function EmptyState({ icon, title, description, action }: {
  icon: ReactNode
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-10 text-center">
      <div className="text-muted-foreground [&_svg]:size-8">{icon}</div>
      <p className="font-medium">{title}</p>
      {description && <p className="max-w-xs text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <h2 className="text-sm font-medium text-muted-foreground">{children}</h2>
      {action}
    </div>
  )
}
