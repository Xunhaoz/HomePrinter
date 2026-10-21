import { useEffect, useMemo, useRef, useState, type FormEvent } from "react"
import {
  CalendarClockIcon, FileTextIcon, ImageIcon, Loader2Icon, MinusIcon, PlusIcon, PrinterIcon, RepeatIcon,
  SparklesIcon, UploadIcon, XIcon,
} from "lucide-react"
import { toast } from "sonner"
import {
  api, type Capabilities, type ColorMode, type Job, type PrintOptions, type Quality, type Scaling,
  type ScheduleInput, type Sides,
} from "@/lib/api"
import { COMMON_MEDIA, WEEKDAYS, errorMessage, formatBytes, isActive, isPhotoMedia, mediaName } from "@/lib/format"
import { cn } from "@/lib/utils"
import { usePoll } from "@/hooks/use-poll"
import { navigate, type Route } from "@/hooks/use-route"
import { Field, PageHeader, Segmented } from "@/components/common"
import { JobCard } from "@/components/jobs"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { NativeSelect, NativeSelectOptGroup, NativeSelectOption } from "@/components/ui/native-select"
import { Switch } from "@/components/ui/switch"

type Kind = "pdf" | "image"
type Mode = "now" | "once" | "cron"
type Repeat = "daily" | "weekly" | "monthly" | "custom"

const A4 = "iso_a4_210x297mm"
const PHOTO_4X6 = "na_index-4x6_4x6in"
const PREFS_KEY = "homeprinter:print-prefs"

function kindOf(file: File): Kind {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf") ? "pdf" : "image"
}

// Only duplex and color are remembered (CUJ §5.2): they're the choices people repeat.
function loadPrefs(): { sides?: Sides; color?: ColorMode } {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}")
  } catch {
    return {}
  }
}

function savePrefs(prefs: { sides: Sides; color: ColorMode }) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs))
  } catch {
    // private mode; preferences just aren't kept
  }
}

function tomorrowAt8(): string {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  d.setHours(8, 0, 0, 0)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T08:00`
}

function buildCron(repeat: Repeat, time: string, weekdays: number[], dayOfMonth: number, custom: string): string {
  if (repeat === "custom") return custom.trim()
  const [h, m] = time.split(":").map(Number)
  if (repeat === "daily") return `${m} ${h} * * *`
  if (repeat === "weekly") return `${m} ${h} * * ${[...weekdays].sort().join(",") || "*"}`
  return `${m} ${h} ${dayOfMonth} * *`
}

export function PrintPage({ route }: { route: Route }) {
  const [prefs] = useState(loadPrefs)
  const [caps, setCaps] = useState<Capabilities>()
  const printer = usePoll(api.printer, 15000)
  const mediaReady = (() => {
    const m = printer.data?.media_ready
    return Array.isArray(m) ? m[0] : m ?? undefined
  })()

  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string>()
  const kind: Kind | null = file ? kindOf(file) : null

  const [copies, setCopies] = useState(1)
  const [media, setMedia] = useState(A4)
  const [mediaTouched, setMediaTouched] = useState(false)
  const [scaling, setScaling] = useState<Scaling>("fit")
  const [scalingTouched, setScalingTouched] = useState(false)
  const [color, setColor] = useState<ColorMode>(prefs.color ?? "auto")
  const [quality, setQuality] = useState<Quality>("normal")
  const [sides, setSides] = useState<Sides>(prefs.sides ?? "one-sided")
  const [pages, setPages] = useState("")
  const [dryRun, setDryRun] = useState(false)

  const [mode, setMode] = useState<Mode>(() => (route.params.get("mode") as Mode) || "now")
  const [name, setName] = useState("")
  const [runAt, setRunAt] = useState(tomorrowAt8)
  const [repeat, setRepeat] = useState<Repeat>("weekly")
  const [time, setTime] = useState("09:00")
  const [weekdays, setWeekdays] = useState<number[]>([1])
  const [dayOfMonth, setDayOfMonth] = useState(1)
  const [customCron, setCustomCron] = useState("")

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const [jobId, setJobId] = useState<string>()
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.capabilities().then(setCaps).catch(() => undefined)
  }, [])

  // Default paper follows what's in the tray (CUJ §5.1) until the user picks one.
  useEffect(() => {
    if (!mediaTouched && mediaReady) setMedia(mediaReady)
  }, [mediaReady, mediaTouched])

  // Photos: fill on photo paper, fit on A4; PDFs: fit. Unless the user chose.
  useEffect(() => {
    if (!scalingTouched) setScaling(kind === "image" && isPhotoMedia(media) ? "fill" : "fit")
  }, [kind, media, scalingTouched])

  // Photos default to high quality, documents to normal (faster, less ink).
  useEffect(() => {
    if (kind) setQuality(kind === "image" ? "high" : "normal")
  }, [kind])

  useEffect(() => {
    if (!file || kindOf(file) !== "image") return setPreview(undefined)
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  // The nozzle-check template from the schedules page.
  useEffect(() => {
    if (route.params.get("template") !== "nozzle") return
    fetch("/nozzle-check.pdf")
      .then((r) => r.blob())
      .then((blob) => {
        setFile(new File([blob], "nozzle-check.pdf", { type: "application/pdf" }))
        setMode("cron")
        setName("噴頭保養")
        setRepeat("weekly")
        setWeekdays([6])
        setTime("10:00")
        setColor("color")
        setSides("one-sided")
      })
      .catch(() => toast.error("載入測試頁失敗"))
  }, [route.params])

  const mediaOptions = useMemo(() => {
    const all = caps?.media ?? COMMON_MEDIA
    const common = COMMON_MEDIA.filter((m) => all.includes(m))
    const rest = all.filter((m) => !common.includes(m) && !m.startsWith("custom_"))
    if (!all.includes(media)) common.unshift(media)
    return { common, rest }
  }, [caps, media])

  function pickFile(next: File | undefined) {
    if (!next) return
    setFile(next)
    setError("")
    setJobId(undefined)
    if (kindOf(next) === "image") setPages("")
  }

  function usePhotoPaper() {
    setMedia(PHOTO_4X6)
    setMediaTouched(true)
    setScaling("fill")
    setQuality("high")
    setColor("color")
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!file || !kind) return
    setSubmitting(true)
    setError("")
    const options: PrintOptions = {
      copies,
      media,
      color_mode: color,
      quality,
      scaling,
      sides: kind === "pdf" ? sides : "one-sided",
      pages: kind === "pdf" ? pages.trim() : null,
      dry_run: dryRun,
    }
    savePrefs({ sides, color })
    try {
      if (mode === "now") {
        const job = await api.print(file, options)
        setJobId(job.id)
        window.scrollTo({ top: 0, behavior: "smooth" })
        toast.success(dryRun ? "試印檢查中" : "已送出列印")
      } else {
        const schedule: ScheduleInput = {
          kind: mode,
          name: name.trim(),
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          ...(mode === "once" ? { run_at: runAt } : { cron: buildCron(repeat, time, weekdays, dayOfMonth, customCron) }),
        }
        await api.createSchedule(file, options, schedule)
        toast.success("排程已建立")
        navigate("/schedules")
      }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  const accept =
    route.params.get("type") === "photo" ? "image/*,.heic,.heif" : route.params.get("type") === "pdf" ? "application/pdf,.pdf" : "application/pdf,.pdf,image/*,.heic,.heif"

  return (
    <form onSubmit={submit} className="space-y-6">
      <PageHeader title="列印" description="照片（JPEG、PNG、HEIC）或 PDF 文件" />

      {jobId && <TrackedJob id={jobId} />}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:items-start">
        {/* File */}
        <div className="space-y-3 lg:sticky lg:top-6">
          <input
            ref={fileInput}
            type="file"
            accept={accept}
            className="sr-only"
            onChange={(e) => {
              pickFile(e.target.files?.[0])
              e.target.value = ""
            }}
          />
          {file ? (
            <Card className="overflow-hidden py-0">
              {preview && (
                <div className="flex max-h-72 items-center justify-center bg-muted">
                  <img src={preview} alt="" className="max-h-72 w-full object-contain" />
                </div>
              )}
              <CardContent className="flex items-center gap-3 py-3.5">
                <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                  {kind === "pdf" ? <FileTextIcon className="size-5" /> : <ImageIcon className="size-5" />}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{file.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {kind === "pdf" ? "PDF 文件" : "照片"} · {formatBytes(file.size)}
                  </p>
                </div>
                <Button type="button" variant="ghost" size="icon-lg" aria-label="換一個檔案" onClick={() => fileInput.current?.click()}>
                  <UploadIcon />
                </Button>
                <Button type="button" variant="ghost" size="icon-lg" aria-label="移除檔案" onClick={() => setFile(null)}>
                  <XIcon />
                </Button>
              </CardContent>
            </Card>
          ) : (
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="flex w-full flex-col items-center gap-3 rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors hover:bg-muted/50 active:bg-muted"
            >
              <div className="flex size-14 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
                <PlusIcon className="size-7" />
              </div>
              <div>
                <p className="font-medium">選擇照片或檔案</p>
                <p className="mt-1 text-sm text-muted-foreground">從照片圖庫、相機或「檔案」App 選取</p>
              </div>
            </button>
          )}
        </div>

        {/* Options */}
        <div className={cn("space-y-6", !file && "pointer-events-none opacity-50")} aria-disabled={!file}>
          <Card>
            <CardContent className="space-y-5">
              <Field label="紙張" htmlFor="media">
                <NativeSelect
                  id="media"
                  value={media}
                  onChange={(e) => {
                    setMedia(e.target.value)
                    setMediaTouched(true)
                  }}
                  className="w-full [&_select]:h-11 [&_select]:text-base"
                >
                  {mediaOptions.common.map((m) => (
                    <NativeSelectOption key={m} value={m}>
                      {mediaName(m)}{m === mediaReady ? "（紙匣中）" : ""}
                    </NativeSelectOption>
                  ))}
                  {mediaOptions.rest.length > 0 && (
                    <NativeSelectOptGroup label="其他尺寸">
                      {mediaOptions.rest.map((m) => (
                        <NativeSelectOption key={m} value={m}>{mediaName(m)}</NativeSelectOption>
                      ))}
                    </NativeSelectOptGroup>
                  )}
                </NativeSelect>
              </Field>
              {kind === "image" && media !== PHOTO_4X6 && (
                <Button type="button" variant="secondary" className="-mt-2 h-9 rounded-full" onClick={usePhotoPaper}>
                  <SparklesIcon />
                  4×6 相片紙（滿版）
                </Button>
              )}

              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-medium">份數</p>
                </div>
                <div className="flex items-center gap-1 rounded-lg border p-1">
                  <Button type="button" variant="ghost" size="icon-lg" aria-label="減少一份" disabled={copies <= 1} onClick={() => setCopies((c) => c - 1)}>
                    <MinusIcon />
                  </Button>
                  <span className="w-8 text-center text-lg font-medium tabular-nums" aria-live="polite">{copies}</span>
                  <Button type="button" variant="ghost" size="icon-lg" aria-label="增加一份" disabled={copies >= 99} onClick={() => setCopies((c) => c + 1)}>
                    <PlusIcon />
                  </Button>
                </div>
              </div>

              <Field label="色彩">
                <Segmented
                  label="色彩"
                  value={color}
                  onChange={setColor}
                  options={[
                    { value: "auto", label: "自動" },
                    { value: "color", label: "彩色" },
                    { value: "monochrome", label: "黑白" },
                  ]}
                />
              </Field>

              {kind !== "image" && (
                <>
                  <Field label="雙面">
                    <Segmented
                      label="雙面"
                      value={sides}
                      onChange={setSides}
                      options={[
                        { value: "one-sided", label: "單面" },
                        { value: "two-sided-long-edge", label: "雙面・長邊" },
                        { value: "two-sided-short-edge", label: "雙面・短邊" },
                      ]}
                    />
                  </Field>
                  <Field label="頁面" htmlFor="pages" hint="留空印全部，例如 1-3,5">
                    <Input
                      id="pages"
                      value={pages}
                      onChange={(e) => setPages(e.target.value)}
                      placeholder="全部"
                      inputMode="numeric"
                      pattern="[0-9,\- ]*"
                      className="h-11 text-base md:text-base"
                    />
                  </Field>
                </>
              )}

              <Collapsible>
                <CollapsibleTrigger asChild>
                  <Button type="button" variant="ghost" className="-mx-2 h-9 text-muted-foreground">更多選項</Button>
                </CollapsibleTrigger>
                <CollapsibleContent className="space-y-5 pt-3">
                  <Field label="品質">
                    <Segmented
                      label="品質"
                      value={quality}
                      onChange={setQuality}
                      options={[
                        { value: "normal", label: "標準" },
                        { value: "high", label: "高品質" },
                      ]}
                    />
                  </Field>
                  <Field
                    label="縮放"
                    hint={scaling === "fill" ? "填滿整張紙，邊緣可能被裁掉" : scaling === "none" ? "照原始尺寸，可能被裁掉或很小" : "完整印出，不裁切"}
                  >
                    <Segmented
                      label="縮放"
                      value={scaling}
                      onChange={(v) => {
                        setScaling(v)
                        setScalingTouched(true)
                      }}
                      options={[
                        { value: "fit", label: "符合紙張" },
                        { value: "fill", label: "滿版" },
                        { value: "none", label: "原尺寸" },
                      ]}
                    />
                  </Field>
                  <label className="flex items-center justify-between gap-4">
                    <span>
                      <span className="block text-sm font-medium">試印檢查</span>
                      <span className="block text-xs text-muted-foreground">只檢查檔案和設定，不會真的印出來</span>
                    </span>
                    <Switch checked={dryRun} onCheckedChange={setDryRun} />
                  </label>
                </CollapsibleContent>
              </Collapsible>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-5">
              <Field label="什麼時候印">
                <Segmented
                  label="什麼時候印"
                  value={mode}
                  onChange={setMode}
                  options={[
                    { value: "now", label: <><PrinterIcon />現在</> },
                    { value: "once", label: <><CalendarClockIcon />指定時間</> },
                    { value: "cron", label: <><RepeatIcon />定期</> },
                  ]}
                />
              </Field>

              {mode !== "now" && (
                <Field label="排程名稱（選填）" htmlFor="name">
                  <Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：週一會議資料" className="h-11 text-base md:text-base" />
                </Field>
              )}

              {mode === "once" && (
                <Field label="時間" htmlFor="run-at">
                  <Input
                    id="run-at"
                    type="datetime-local"
                    value={runAt}
                    onChange={(e) => setRunAt(e.target.value)}
                    required
                    className="h-11 text-base md:text-base"
                  />
                </Field>
              )}

              {mode === "cron" && (
                <>
                  <Field label="重複">
                    <Segmented
                      label="重複"
                      value={repeat}
                      onChange={setRepeat}
                      options={[
                        { value: "daily", label: "每天" },
                        { value: "weekly", label: "每週" },
                        { value: "monthly", label: "每月" },
                        { value: "custom", label: "自訂" },
                      ]}
                    />
                  </Field>
                  {repeat === "weekly" && (
                    <div className="grid grid-cols-7 gap-1.5" role="group" aria-label="星期">
                      {WEEKDAYS.map((label, day) => {
                        const on = weekdays.includes(day)
                        return (
                          <button
                            key={day}
                            type="button"
                            aria-pressed={on}
                            onClick={() => setWeekdays((w) => (on ? w.filter((d) => d !== day) : [...w, day]))}
                            className={cn(
                              "h-11 rounded-lg border text-sm transition-colors",
                              on ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted",
                            )}
                          >
                            {label}
                          </button>
                        )
                      })}
                    </div>
                  )}
                  {repeat === "monthly" && (
                    <Field label="日期" htmlFor="dom">
                      <NativeSelect id="dom" value={dayOfMonth} onChange={(e) => setDayOfMonth(Number(e.target.value))} className="w-full [&_select]:h-11 [&_select]:text-base">
                        {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => (
                          <NativeSelectOption key={d} value={d}>每月 {d} 日</NativeSelectOption>
                        ))}
                      </NativeSelect>
                    </Field>
                  )}
                  {repeat === "custom" ? (
                    <Field label="Cron 表達式" htmlFor="cron" hint="分 時 日 月 週，例如 0 9 * * 1 = 每週一 09:00">
                      <Input
                        id="cron"
                        value={customCron}
                        onChange={(e) => setCustomCron(e.target.value)}
                        placeholder="0 9 * * 1"
                        autoCapitalize="none"
                        autoCorrect="off"
                        spellCheck={false}
                        required
                        className="h-11 font-mono text-base md:text-base"
                      />
                    </Field>
                  ) : (
                    <Field label="時間" htmlFor="time">
                      <Input id="time" type="time" value={time} onChange={(e) => setTime(e.target.value)} required className="h-11 text-base md:text-base" />
                    </Field>
                  )}
                </>
              )}
            </CardContent>
          </Card>

          {error && (
            <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2.5 text-sm text-destructive">{error}</p>
          )}

          <div className="sticky bottom-[calc(var(--nav-h)+0.75rem)] z-10 lg:static">
            <Button
              type="submit"
              className="h-12 w-full text-base shadow-lg lg:shadow-none"
              disabled={!file || submitting || (mode === "cron" && repeat === "weekly" && weekdays.length === 0)}
            >
              {submitting ? <Loader2Icon className="animate-spin" /> : mode === "now" ? <PrinterIcon /> : <CalendarClockIcon />}
              {submitting ? "上傳中…" : mode === "now" ? (dryRun ? "試印檢查" : copies > 1 ? `列印 ${copies} 份` : "列印") : "建立排程"}
            </Button>
          </div>
        </div>
      </div>
    </form>
  )
}

function TrackedJob({ id }: { id: string }) {
  const job = usePoll<Job | null>(
    () => api.getJob(id).catch(() => null),
    (data) => (data && isActive(data.status) ? 2000 : null),
  )
  if (!job.data) return null
  return <JobCard job={job.data} onChange={job.refresh} />
}
