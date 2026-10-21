import { useEffect, useState } from "react"
import {
  CheckCircle2Icon, DownloadIcon, FileArchiveIcon, FileTextIcon, ImageIcon, InboxIcon, Loader2Icon, ScanLineIcon,
  Trash2Icon,
} from "lucide-react"
import { toast } from "sonner"
import { ApiError, api, type Scan, type ScanSettings } from "@/lib/api"
import { errorMessage, formatBytes, formatWhen, relativeDays } from "@/lib/format"
import { usePoll } from "@/hooks/use-poll"
import { EmptyState, Field, PageHeader, SectionTitle, Segmented, StatusBadge } from "@/components/common"
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"

type Preset = "document" | "color" | "photo"

const PRESETS: Record<Preset, Omit<ScanSettings, "source">> = {
  document: { color_mode: "grayscale", resolution: 200, format: "pdf", size: "a4" },
  color: { color_mode: "color", resolution: 300, format: "pdf", size: "a4" },
  photo: { color_mode: "color", resolution: 600, format: "jpeg", size: "max" },
}

const SCANNER_STATE: Record<string, string> = {
  Idle: "待機",
  Processing: "掃描中",
  Testing: "準備中",
  Stopped: "已停止",
  Down: "無法使用",
}

function FileIcon({ scan }: { scan: Scan }) {
  if (scan.media_type === "application/zip") return <FileArchiveIcon className="size-5" />
  return scan.format === "jpeg" ? <ImageIcon className="size-5" /> : <FileTextIcon className="size-5" />
}

export function ScanPage() {
  const scanner = usePoll(api.scanner, (data) => (data?.busy ? 5000 : 30000))
  const scans = usePoll(api.scans, (data) => (data?.some((s) => s.status === "scanning") ? 2000 : null))

  const [preset, setPreset] = useState<Preset | "custom">("document")
  const [settings, setSettings] = useState<ScanSettings>({ source: "flatbed", ...PRESETS.document })
  const [sourceTouched, setSourceTouched] = useState(false)
  const [starting, setStarting] = useState(false)
  const [currentId, setCurrentId] = useState<string>()

  const feederLoaded = scanner.data?.adf_state === "ScannerAdfLoaded"
  const hasFeeder = !!scanner.data?.sources.feeder
  const caps = scanner.data?.sources[settings.source]
  const resolutions = (caps?.resolutions ?? [100, 200, 300, 600]).filter((r) => r >= 100)

  // Use the feeder automatically when paper is loaded in it (CUJ §5.3).
  useEffect(() => {
    if (!sourceTouched && scanner.data) setSettings((s) => ({ ...s, source: feederLoaded ? "feeder" : "flatbed" }))
  }, [feederLoaded, sourceTouched, scanner.data])

  function update(patch: Partial<ScanSettings>) {
    setPreset("custom")
    setSettings((s) => ({ ...s, ...patch }))
  }

  function choosePreset(p: Preset | "custom") {
    setPreset(p)
    if (p !== "custom") setSettings((s) => ({ ...s, ...PRESETS[p], size: s.source === "feeder" && p === "photo" ? "a4" : PRESETS[p].size }))
  }

  async function start() {
    setStarting(true)
    try {
      const resolution = resolutions.includes(settings.resolution) ? settings.resolution : 300
      const scan = await api.scan({ ...settings, resolution })
      setCurrentId(scan.id)
      await scans.refresh()
    } catch (err) {
      const message = errorMessage(err)
      toast.error(err instanceof ApiError && err.status === 409 ? message : "無法開始掃描", {
        description: err instanceof ApiError && err.status === 409 ? undefined : message,
      })
      scanner.refresh()
    } finally {
      setStarting(false)
    }
  }

  const list = scans.data ?? []
  const current = list.find((s) => s.id === currentId)
  const scanning = list.some((s) => s.status === "scanning")
  const busy = scanning || starting || !!scanner.data?.busy
  const stateLabel = scanner.data?.state ? SCANNER_STATE[scanner.data.state] ?? scanner.data.state : undefined

  return (
    <div className="space-y-6">
      <PageHeader
        title="掃描"
        description={settings.source === "flatbed" ? "把文件正面朝下放在玻璃上" : "把文件正面朝上放進上方送紙器"}
        action={
          scanner.error && !scanner.data ? (
            <StatusBadge tone="error">離線</StatusBadge>
          ) : stateLabel ? (
            <StatusBadge tone={scanner.data?.state === "Idle" ? "ok" : scanner.data?.busy ? "busy" : "warn"} pulse={scanner.data?.busy}>
              {stateLabel}
            </StatusBadge>
          ) : undefined
        }
      />

      {current && <CurrentScan scan={current} onDismiss={() => setCurrentId(undefined)} />}

      <Card>
        <CardContent className="space-y-5">
          {hasFeeder && (
            <Field
              label="放在哪裡"
              hint={settings.source === "feeder" && !feederLoaded ? "送紙器目前沒有紙" : settings.source === "feeder" ? "可一次掃多頁，只掃單面" : undefined}
            >
              <Segmented
                label="來源"
                value={settings.source}
                onChange={(source) => {
                  setSourceTouched(true)
                  setSettings((s) => ({ ...s, source, resolution: source === "feeder" && s.resolution > 600 ? 600 : s.resolution }))
                }}
                options={[
                  { value: "flatbed", label: "玻璃平台" },
                  { value: "feeder", label: feederLoaded ? "送紙器 ●" : "送紙器" },
                ]}
              />
            </Field>
          )}

          <Field label="用途">
            <Segmented
              label="用途"
              value={preset}
              onChange={choosePreset}
              options={[
                { value: "document", label: "文件" },
                { value: "color", label: "彩色文件" },
                { value: "photo", label: "照片" },
                { value: "custom", label: "自訂" },
              ]}
            />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="色彩" htmlFor="scan-color">
              <NativeSelect id="scan-color" value={settings.color_mode} onChange={(e) => update({ color_mode: e.target.value as ScanSettings["color_mode"] })} className="w-full [&_select]:h-11 [&_select]:text-base">
                <NativeSelectOption value="color">彩色</NativeSelectOption>
                <NativeSelectOption value="grayscale">灰階</NativeSelectOption>
                <NativeSelectOption value="bw">黑白</NativeSelectOption>
              </NativeSelect>
            </Field>
            <Field label="解析度" htmlFor="scan-dpi">
              <NativeSelect id="scan-dpi" value={settings.resolution} onChange={(e) => update({ resolution: Number(e.target.value) })} className="w-full [&_select]:h-11 [&_select]:text-base">
                {resolutions.map((r) => (
                  <NativeSelectOption key={r} value={r}>{r} dpi</NativeSelectOption>
                ))}
              </NativeSelect>
            </Field>
            <Field label="格式" htmlFor="scan-format">
              <NativeSelect id="scan-format" value={settings.format} onChange={(e) => update({ format: e.target.value as ScanSettings["format"] })} className="w-full [&_select]:h-11 [&_select]:text-base">
                <NativeSelectOption value="pdf">PDF</NativeSelectOption>
                <NativeSelectOption value="jpeg">JPEG</NativeSelectOption>
              </NativeSelect>
            </Field>
            <Field label="尺寸" htmlFor="scan-size">
              <NativeSelect id="scan-size" value={settings.size} onChange={(e) => update({ size: e.target.value as ScanSettings["size"] })} className="w-full [&_select]:h-11 [&_select]:text-base">
                <NativeSelectOption value="a4">A4</NativeSelectOption>
                <NativeSelectOption value="letter">Letter</NativeSelectOption>
                <NativeSelectOption value="legal">Legal</NativeSelectOption>
                <NativeSelectOption value="max">整個平台</NativeSelectOption>
              </NativeSelect>
            </Field>
          </div>
          {settings.source === "feeder" && settings.format === "jpeg" && (
            <p className="text-xs text-muted-foreground">多頁的 JPEG 會打包成一個 zip 檔。</p>
          )}
        </CardContent>
      </Card>

      <div className="sticky bottom-[calc(var(--nav-h)+0.75rem)] z-10 lg:static">
        <Button
          className="h-12 w-full text-base shadow-lg lg:shadow-none"
          onClick={start}
          disabled={busy || (settings.source === "feeder" && !feederLoaded)}
        >
          {busy ? <Loader2Icon className="animate-spin" /> : <ScanLineIcon />}
          {scanning ? "掃描中…" : starting ? "準備中…" : "開始掃描"}
        </Button>
      </div>

      <section className="space-y-3">
        <SectionTitle>掃描紀錄</SectionTitle>
        {list.filter((s) => s.id !== currentId).length > 0 ? (
          <div className="divide-y rounded-xl border bg-card">
            {list.filter((s) => s.id !== currentId).map((scan) => (
              <ScanRow key={scan.id} scan={scan} onDeleted={scans.refresh} />
            ))}
          </div>
        ) : (
          !scans.loading && !current && (
            <EmptyState icon={<InboxIcon />} title="還沒有掃描檔" description="掃描檔會保留 7 天，之後自動刪除。" />
          )
        )}
      </section>
    </div>
  )
}

// The scan just started: shows progress, then a result card with a download button.
function CurrentScan({ scan, onDismiss }: { scan: Scan; onDismiss: () => void }) {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (scan.status !== "scanning") return
    const started = new Date(scan.created_at).getTime()
    const timer = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 500)
    return () => clearInterval(timer)
  }, [scan.status, scan.created_at])

  if (scan.status === "scanning") {
    return (
      <Card>
        <CardContent className="flex items-center gap-4">
          <div className="relative flex size-12 items-center justify-center rounded-xl bg-sky-500/12 text-sky-600 dark:text-sky-400">
            <ScanLineIcon className="size-6 animate-pulse" />
          </div>
          <div className="flex-1">
            <p className="font-medium">掃描中…</p>
            <p className="text-sm text-muted-foreground tabular-nums">已經過 {elapsed} 秒，通常需要 15–30 秒</p>
          </div>
        </CardContent>
      </Card>
    )
  }

  if (scan.status === "failed") {
    return (
      <Card className="border-destructive/30">
        <CardContent className="space-y-3">
          <p className="font-medium text-destructive">掃描失敗</p>
          <p className="text-sm text-muted-foreground">{errorMessage(scan.error)}</p>
          <Button variant="outline" className="h-10" onClick={onDismiss}>知道了</Button>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="overflow-hidden py-0">
      {scan.media_type === "image/jpeg" && (
        <div className="flex max-h-80 items-center justify-center bg-muted">
          <img src={api.scanFileUrl(scan.id)} alt="掃描結果" className="max-h-80 w-full object-contain" />
        </div>
      )}
      <CardContent className="space-y-4 py-4">
        <div className="flex items-center gap-3">
          <CheckCircle2Icon className="size-6 shrink-0 text-emerald-600 dark:text-emerald-400" />
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">{scan.filename}</p>
            <p className="text-xs text-muted-foreground">
              {scan.pages} 頁 · {formatBytes(scan.size_bytes)}
            </p>
          </div>
        </div>
        <Button asChild className="h-11 w-full">
          <a href={api.scanFileUrl(scan.id)} download={scan.filename ?? undefined}>
            <DownloadIcon />
            下載
          </a>
        </Button>
      </CardContent>
    </Card>
  )
}

function ScanRow({ scan, onDeleted }: { scan: Scan; onDeleted: () => void }) {

  async function remove() {
    try {
      await api.deleteScan(scan.id)
      onDeleted()
    } catch (err) {
      toast.error("刪除失敗", { description: errorMessage(err) })
    }
  }

  return (
    <div className="flex items-center gap-3 p-3">
      <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <FileIcon scan={scan} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{scan.filename ?? "掃描中…"}</p>
        <p className="text-xs text-muted-foreground">
          {scan.status === "failed"
            ? `失敗：${errorMessage(scan.error)}`
            : [formatWhen(scan.created_at), scan.pages && `${scan.pages} 頁`, formatBytes(scan.size_bytes), relativeDays(scan.expires_at)]
                .filter(Boolean)
                .join(" · ")}
        </p>
      </div>
      {scan.status === "completed" && (
        <Button asChild variant="ghost" size="icon-lg" aria-label="下載">
          <a href={api.scanFileUrl(scan.id)} download={scan.filename ?? undefined}>
            <DownloadIcon />
          </a>
        </Button>
      )}
      {scan.status !== "scanning" && (
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="ghost" size="icon-lg" aria-label="刪除" className="text-muted-foreground">
              <Trash2Icon />
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>刪除這個掃描檔？</AlertDialogTitle>
              <AlertDialogDescription>{scan.filename} 會永久刪除。</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              <AlertDialogAction variant="destructive" onClick={remove}>刪除</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </div>
  )
}
