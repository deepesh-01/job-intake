import { useState } from "react"
import { JobsList } from "./components/JobsList"
import { FilterBar, type Filters, type ViewMode } from "./components/FilterBar"
import { JobDetail } from "./components/JobDetail"
import { ProcessorButton } from "./components/ProcessorButton"
import { Header } from "./components/Header"
import { PreviewBanner } from "./components/PreviewBanner"
import { CardStack } from "./components/CardStack"

export function App() {
  const [filters, setFilters] = useState<Filters>({
    status: ["new"],
    resumeStrong: false,  // opt-in via the slider — default OFF so the
                           // status filter alone drives what's visible.
    targetCity: false,
    excludeNonUsOnly: false,
    hasComp: false,
    q: "",
    sort: "resume_match_desc",
    discoveredWithin: "",
  })
  const [view, setView] = useState<ViewMode>("list")
  const [openId, setOpenId] = useState<string | null>(null)

  // Switching to cards = "I want to triage new ones." Force the status
  // filter so the user sees the same thing the server is giving them.
  // Switching back to list keeps whatever the user changes it to.
  const handleViewChange = (next: ViewMode) => {
    if (next === "cards") {
      setFilters((f) => ({ ...f, status: ["new"] }))
    }
    setView(next)
  }

  return (
    <div className="min-h-screen flex flex-col">
      {/* Single sticky stack — PreviewBanner (if viewer) + Header +
          FilterBar all pin together at the top of the viewport. */}
      <div className="sticky top-0 z-40">
        <PreviewBanner />
        <Header />
        <FilterBar value={filters} onChange={setFilters} view={view} onViewChange={handleViewChange} />
      </div>
      <main className="flex-1 container mx-auto pb-32 pt-2">
        {view === "list" ? (
          <JobsList filters={filters} onOpen={setOpenId} />
        ) : (
          <CardStack filters={filters} onOpen={setOpenId} />
        )}
      </main>
      <ProcessorButton />
      <JobDetail id={openId} onClose={() => setOpenId(null)} />
    </div>
  )
}
