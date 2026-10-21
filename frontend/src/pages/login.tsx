import { useState, type FormEvent } from "react"
import { ArrowRightIcon, Loader2Icon, PrinterIcon } from "lucide-react"
import { ApiError, api } from "@/lib/api"
import { errorMessage } from "@/lib/format"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [code, setCode] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!code.trim()) return
    setBusy(true)
    setError("")
    try {
      await api.login(code)
      onLogin()
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setError("通行碼不正確")
      else if (err instanceof ApiError && err.status === 429) {
        const minutes = Math.max(1, Math.ceil((err.retryAfter ?? 900) / 60))
        setError(`錯太多次了，請 ${minutes} 分鐘後再試`)
      } else setError(errorMessage(err))
      setCode("")
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center px-6 pt-safe pb-safe">
      <form onSubmit={submit} className="flex w-full max-w-xs flex-col items-center gap-6">
        <div className="flex size-16 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-sm">
          <PrinterIcon className="size-8" />
        </div>
        <div className="text-center">
          <h1 className="text-2xl font-semibold tracking-tight">HomePrinter</h1>
          <p className="mt-1 text-sm text-muted-foreground">輸入通行碼以使用家裡的印表機</p>
        </div>
        <div className="w-full space-y-3">
          <Input
            name="code"
            aria-label="通行碼"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="通行碼"
            autoFocus
            autoComplete="current-password"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="go"
            maxLength={64}
            aria-invalid={!!error}
            className="h-14 text-center font-mono text-2xl tracking-[0.4em] uppercase placeholder:font-sans placeholder:text-base placeholder:tracking-normal placeholder:normal-case md:text-2xl"
          />
          {error && <p role="alert" className="text-center text-sm text-destructive">{error}</p>}
          <Button type="submit" className="h-12 w-full text-base" disabled={busy || !code.trim()}>
            {busy ? <Loader2Icon className="animate-spin" /> : <ArrowRightIcon />}
            進入
          </Button>
        </div>
      </form>
    </main>
  )
}
