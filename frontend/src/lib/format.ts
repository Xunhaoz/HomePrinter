import type { JobStatus, PrinterStatus, Schedule } from "@/lib/api"

// Friendly names for the paper sizes people actually use at home; the rest are derived from the PWG name.
const MEDIA_NAMES: Record<string, string> = {
  "iso_a4_210x297mm": "A4",
  "iso_a5_148x210mm": "A5",
  "iso_a6_105x148mm": "A6",
  "iso_b5_176x250mm": "B5 (ISO)",
  "jis_b5_182x257mm": "B5",
  "na_letter_8.5x11in": "Letter",
  "na_legal_8.5x14in": "Legal",
  "na_index-4x6_4x6in": "4×6 相片",
  "na_5x7_5x7in": "5×7 相片",
  "na_govt-letter_8x10in": "8×10 相片",
  "oe_photo-l_3.5x5in": "L 相片 (3.5×5)",
  "na_index-3x5_3x5in": "3×5 卡片",
  "jpn_hagaki_100x148mm": "明信片",
  "om_hagaki_100x148mm": "明信片",
  "na_number-10_4.125x9.5in": "#10 信封",
  "iso_dl_110x220mm": "DL 信封",
}

// Shown first in the paper picker, in this order.
export const COMMON_MEDIA = [
  "iso_a4_210x297mm",
  "na_index-4x6_4x6in",
  "oe_photo-l_3.5x5in",
  "na_5x7_5x7in",
  "iso_a5_148x210mm",
  "na_letter_8.5x11in",
]

export function mediaName(media: string): string {
  if (MEDIA_NAMES[media]) return MEDIA_NAMES[media]
  // e.g. "na_foo_8x10in" -> "8×10 in"
  const size = media.split("_").pop() ?? media
  const match = size.match(/^([\d.]+)x([\d.]+)(mm|in)$/)
  return match ? `${match[1]}×${match[2]} ${match[3]}` : media
}

export function isPhotoMedia(media: string | null | undefined): boolean {
  return !!media && /4x6|5x7|8x10|photo|3\.5x5|hagaki|3x5/.test(media)
}

export const JOB_STATUS: Record<JobStatus, string> = {
  queued: "排隊中",
  rendering: "處理檔案中",
  printing: "列印中",
  completed: "已完成",
  failed: "失敗",
  canceled: "已取消",
}

export function isActive(status: JobStatus): boolean {
  return status === "queued" || status === "rendering" || status === "printing"
}

export const PRINTER_STATE: Record<PrinterStatus["state"], string> = {
  idle: "待機",
  processing: "工作中",
  stopped: "已停止",
  unknown: "未知",
}

// printer-state-reasons worth telling a person about; suffixes (-error, -warning, -report) are stripped.
const REASONS: Record<string, string> = {
  "media-empty": "紙匣沒有紙",
  "media-needed": "需要放紙",
  "media-jam": "卡紙",
  "media-low": "紙快用完了",
  "cover-open": "蓋子沒關好",
  "door-open": "外蓋沒關好",
  "input-tray-missing": "紙匣沒裝好",
  "marker-supply-low": "墨水偏低",
  "marker-supply-empty": "墨水用完了",
  "marker-waste-almost-full": "廢墨收集盒快滿了",
  "marker-waste-full": "廢墨收集盒已滿",
  "offline": "印表機離線",
  "paused": "印表機已暫停",
  "toner-low": "墨水偏低",
  "toner-empty": "墨水用完了",
}

export function stateReasons(reasons: string[]): { text: string; severe: boolean }[] {
  return reasons
    .filter((r) => r !== "none")
    .map((r) => {
      const base = r.replace(/-(error|warning|report)$/, "")
      return { text: REASONS[base] ?? r, severe: r.endsWith("-error") }
    })
}

export function inkName(name: string): string {
  const lower = name.toLowerCase()
  if (lower.includes("photo black")) return "相片黑"
  if (lower.includes("black")) return "黑"
  if (lower.includes("cyan")) return "青"
  if (lower.includes("magenta")) return "洋紅"
  if (lower.includes("yellow")) return "黃"
  return name
}

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return ""
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const timeFmt = new Intl.DateTimeFormat("zh-TW", { hour: "2-digit", minute: "2-digit", hour12: false })
const dateTimeFmt = new Intl.DateTimeFormat("zh-TW", {
  month: "numeric",
  day: "numeric",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
})

export function formatWhen(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return ""
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value)
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  return sameDay ? `今天 ${timeFmt.format(date)}` : dateTimeFmt.format(date)
}

export function relativeDays(iso: string): string {
  const days = Math.ceil((new Date(iso).getTime() - Date.now()) / 86400000)
  if (days <= 0) return "今天到期"
  return `${days} 天後刪除`
}

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"]

// Readable text for the cron shapes the schedule builder makes; anything else is shown raw.
export function describeCron(cron: string): string {
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return cron
  const [min, hour, dom, mon, dow] = parts
  if (!/^\d+$/.test(min) || !/^\d+$/.test(hour) || mon !== "*") return `cron: ${cron}`
  const time = `${hour.padStart(2, "0")}:${min.padStart(2, "0")}`
  if (dom === "*" && dow === "*") return `每天 ${time}`
  if (dom === "*" && /^[0-7](,[0-7])*$/.test(dow)) {
    const days = dow.split(",").map((d) => WEEKDAYS[Number(d) % 7])
    return `每週${days.join("、")} ${time}`
  }
  if (/^\d+$/.test(dom) && dow === "*") return `每月 ${dom} 日 ${time}`
  return `cron: ${cron}`
}

export function describeSchedule(s: Schedule): string {
  if (s.kind === "cron" && s.cron) return describeCron(s.cron)
  return s.run_at ? `一次：${formatWhen(s.run_at)}` : "一次"
}

export { WEEKDAYS }

// The backend's messages are English; translate the ones people actually run into.
const ERRORS: [RegExp, string][] = [
  [/busy/, "掃描器正在忙，請稍後再試"],
  [/feeder is empty/, "送紙器沒有放紙"],
  [/scanner is (Processing|Testing|Stopped|Down)/, "掃描器目前無法使用"],
  [/cannot (talk to|resolve)|timed out|TimeoutError/i, "連不到印表機，請確認它有開機並連上網路"],
  [/two-sided printing needs a PDF/, "照片只能單面列印"],
  [/'pages' only applies/, "只有 PDF 可以指定頁碼"],
  [/not a readable/, "無法讀取這個檔案，請選 PDF 或照片"],
  [/password-protected/, "不支援有密碼保護的 PDF"],
  [/bad page range|outside 1-/, "頁碼範圍不正確"],
  [/run_at is in the past|already ran or its time has passed/, "這個時間已經過了"],
  [/invalid cron/, "重複規則格式不正確"],
  [/larger than/, "檔案太大"],
  [/empty file/, "檔案是空的"],
  [/too many wrong codes/, "錯太多次了，請稍後再試"],
]

export function errorMessage(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err)
  return ERRORS.find(([pattern]) => pattern.test(message))?.[1] ?? message
}
