import { Eye } from "lucide-react"
import { useAuth } from "@/lib/auth"

export function PreviewBanner() {
  const { canMutate, isReady } = useAuth()
  if (!isReady || canMutate) return null

  // Non-sticky — sits at the very top of the document. Header below it
  // is sticky-top-0 so it takes over once the banner scrolls past.
  return (
    <div className="bg-amber-500/15 border-b border-amber-500/30 backdrop-blur-xl">
      <div className="container mx-auto min-h-9 py-2 flex items-center justify-center gap-2 text-[12px] text-amber-200/95 text-center">
        <Eye className="size-3.5 shrink-0" />
        <span className="font-medium">Preview mode</span>
        <span className="text-amber-200/70 hidden sm:inline">— actions are disabled. Only the owner can change job status.</span>
        <span className="text-amber-200/70 sm:hidden">— actions disabled</span>
      </div>
    </div>
  )
}
