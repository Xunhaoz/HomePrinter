import { FileTextIcon, ImageIcon, InboxIcon, ScanLineIcon } from "lucide-react"
import { api } from "@/lib/api"
import { isActive } from "@/lib/format"
import { usePoll } from "@/hooks/use-poll"
import { EmptyState, PageHeader, SectionTitle } from "@/components/common"
import { JobCard } from "@/components/jobs"
import { PrinterCard } from "@/components/printer"
import { Button } from "@/components/ui/button"

const QUICK = [
  { href: "#/print?type=photo", icon: ImageIcon, label: "印照片" },
  { href: "#/print?type=pdf", icon: FileTextIcon, label: "印文件" },
  { href: "#/scan", icon: ScanLineIcon, label: "掃描" },
]

export function HomePage() {
  const printer = usePoll(api.printer, 15000)
  const jobs = usePoll(api.jobs, (data) => (data?.some((j) => isActive(j.status)) ? 2000 : 30000))

  const list = jobs.data ?? []
  const active = list.filter((j) => isActive(j.status))
  const recent = list.filter((j) => !isActive(j.status)).slice(0, 5)

  return (
    <div className="space-y-6">
      <PageHeader title="HomePrinter" description="家裡的印表機，隨時隨地都能用" />

      <PrinterCard status={printer.data} error={printer.error} loading={printer.loading} />

      <div className="grid grid-cols-3 gap-3">
        {QUICK.map(({ href, icon: Icon, label }) => (
          <Button key={href} asChild variant="outline" className="h-20 flex-col gap-2 rounded-xl text-sm">
            <a href={href}>
              <Icon className="size-6" />
              {label}
            </a>
          </Button>
        ))}
      </div>

      {active.length > 0 && (
        <section className="space-y-3">
          <SectionTitle>進行中</SectionTitle>
          {active.map((job) => <JobCard key={job.id} job={job} onChange={jobs.refresh} />)}
        </section>
      )}

      <section className="space-y-3">
        <SectionTitle>最近的列印</SectionTitle>
        {recent.length > 0 ? (
          recent.map((job) => <JobCard key={job.id} job={job} />)
        ) : (
          !jobs.loading && active.length === 0 && (
            <EmptyState
              icon={<InboxIcon />}
              title="還沒有列印紀錄"
              description="列印紀錄只保留到服務重新啟動前。"
            />
          )
        )}
      </section>
    </div>
  )
}
