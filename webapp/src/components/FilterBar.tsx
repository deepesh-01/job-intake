import { useState } from "react"
import { Search, SlidersHorizontal, X } from "lucide-react"
import { cn } from "@/lib/utils"
import { motion, AnimatePresence } from "framer-motion"

export interface Filters {
  status: string[]
  resumeStrong: boolean
  targetCity: boolean
  excludeNonUsOnly: boolean
  q: string
  sort: string
}

const STATUS_OPTIONS = [
  { value: "new", label: "New" },
  { value: "tailor", label: "Queued" },
  { value: "ready", label: "Ready" },
  { value: "applied", label: "Applied" },
  { value: "rejected", label: "Rejected" },
  { value: "skip", label: "Skipped" },
]

const SORT_OPTIONS = [
  { value: "resume_match_desc", label: "Best match" },
  { value: "discovered_desc", label: "Newest" },
  { value: "comp_high_desc", label: "Highest comp" },
]

export function FilterBar({
  value,
  onChange,
}: {
  value: Filters
  onChange: (f: Filters) => void
}) {
  const [showAdvanced, setShowAdvanced] = useState(false)

  const toggleStatus = (s: string) => {
    const has = value.status.includes(s)
    onChange({
      ...value,
      status: has ? value.status.filter((x) => x !== s) : [...value.status, s],
    })
  }

  return (
    <div className="backdrop-blur-xl bg-background/70 border-b border-border/60">
      <div className="container mx-auto py-3 space-y-2.5">
        {/* status pills */}
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {STATUS_OPTIONS.map((opt) => {
            const active = value.status.includes(opt.value)
            return (
              <button
                key={opt.value}
                onClick={() => toggleStatus(opt.value)}
                className={cn(
                  "shrink-0 px-3 py-1.5 rounded-full text-xs font-medium border transition-all",
                  "active:scale-95",
                  active
                    ? "bg-foreground text-background border-foreground shadow-sm"
                    : "bg-transparent text-muted-foreground border-border hover:text-foreground hover:border-foreground/50",
                )}
              >
                {opt.label}
              </button>
            )
          })}
          <div className="grow" />
          <button
            onClick={() => setShowAdvanced((s) => !s)}
            className={cn(
              "shrink-0 size-8 grid place-items-center rounded-full text-muted-foreground hover:text-foreground transition-colors",
              showAdvanced && "bg-muted text-foreground",
            )}
            aria-label="Toggle filters"
          >
            <SlidersHorizontal className="size-4" />
          </button>
        </div>

        {/* search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none" />
          <input
            type="text"
            value={value.q}
            onChange={(e) => onChange({ ...value, q: e.target.value })}
            placeholder="Search company, role, location…"
            className="w-full h-10 pl-9 pr-9 text-sm bg-muted/40 border border-transparent focus:border-ring focus:bg-background rounded-xl outline-none transition-all placeholder:text-muted-foreground"
          />
          {value.q && (
            <button
              onClick={() => onChange({ ...value, q: "" })}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 size-6 grid place-items-center rounded-full text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              aria-label="Clear search"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>

        <AnimatePresence initial={false}>
          {showAdvanced && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
              className="overflow-hidden"
            >
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <ToggleChip
                  active={value.resumeStrong}
                  onClick={() => onChange({ ...value, resumeStrong: !value.resumeStrong })}
                  label="Resume-strong"
                />
                <ToggleChip
                  active={value.targetCity}
                  onClick={() => onChange({ ...value, targetCity: !value.targetCity })}
                  label="Target city"
                />
                <ToggleChip
                  active={value.excludeNonUsOnly}
                  onClick={() => onChange({ ...value, excludeNonUsOnly: !value.excludeNonUsOnly })}
                  label="Hide US-only"
                />
                <div className="grow" />
                <select
                  value={value.sort}
                  onChange={(e) => onChange({ ...value, sort: e.target.value })}
                  className="h-8 text-xs bg-muted/40 border border-transparent focus:border-ring rounded-lg px-2 outline-none"
                >
                  {SORT_OPTIONS.map((s) => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </select>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

function ToggleChip({
  active,
  onClick,
  label,
}: {
  active: boolean
  onClick: () => void
  label: string
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "px-3 h-8 rounded-full text-xs font-medium border transition-all active:scale-95",
        active
          ? "bg-primary/15 text-primary border-primary/30"
          : "bg-transparent text-muted-foreground border-border hover:text-foreground",
      )}
    >
      {label}
    </button>
  )
}
