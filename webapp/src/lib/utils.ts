import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatRelativeDate(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const diffMs = Date.now() - d.getTime()
  const days = Math.round(diffMs / 86400000)
  if (days === 0) return "today"
  if (days === 1) return "yesterday"
  if (days < 7) return `${days}d ago`
  if (days < 30) return `${Math.round(days / 7)}w ago`
  if (days < 365) return `${Math.round(days / 30)}mo ago`
  return d.toISOString().slice(0, 10)
}

// Date-only ISO strings (YYYY-MM-DD) get treated as midnight UTC by the
// Date constructor, which would invent a misleading local time. Detect
// that shape so we can render the date without a fake time component.
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/

// Compact local datetime — for full-ISO inputs, prefer "Today, 11:32 AM"
// or "Yesterday, 11:32 PM" so recent items read naturally. Falls back
// to "Apr 27, 11:32 AM" (this year) or "Apr 27, 2025" (different year).
// Date-only inputs (YYYY-MM-DD) drop the time so we don't fake one.
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const dateOnly = DATE_ONLY_RE.test(iso)
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const now = new Date()

  if (dateOnly) {
    // Read UTC parts so the rendered calendar day matches what was stored.
    const sameYear = d.getUTCFullYear() === now.getFullYear()
    return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
      .toLocaleDateString(undefined, sameYear
        ? { month: "short", day: "numeric", timeZone: "UTC" }
        : { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
  }

  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })

  // Today / yesterday in the local TZ — so a row added at 23:50 local
  // doesn't read "yesterday" just because it was last calendar day UTC.
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startOfRow = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const dayDiff = Math.round(
    (startOfToday.getTime() - startOfRow.getTime()) / 86400000,
  )
  if (dayDiff === 0) return `Today, ${time}`
  if (dayDiff === 1) return `Yesterday, ${time}`

  const sameYear = d.getFullYear() === now.getFullYear()
  if (sameYear) {
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    })
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
}

// Full local datetime for tooltips, e.g. "Mon, Apr 27, 2026, 11:32 AM IST".
// For date-only inputs we drop the time so the tooltip doesn't fake one.
export function formatDateTimeFull(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  if (DATE_ONLY_RE.test(iso)) {
    return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
      .toLocaleDateString(undefined, {
        weekday: "short",
        year: "numeric",
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      }) + " (date only)"
  }
  return d.toLocaleString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  })
}

export function formatComp(
  currency: string | null,
  high: number | null,
  highUsd: number | null,
): string {
  if (!currency || (!high && !highUsd)) return ""
  if (currency === "INR" && high) {
    if (high >= 10_000_000) return `₹${(high / 10_000_000).toFixed(1)}Cr`
    if (high >= 100_000) return `₹${(high / 100_000).toFixed(0)}L`
    return `₹${high}`
  }
  const v = high ?? highUsd ?? 0
  const sym: Record<string, string> = { USD: "$", GBP: "£", EUR: "€", CAD: "C$", AUD: "A$", SGD: "S$" }
  const s = sym[currency] || currency + " "
  if (v >= 1000) return `${s}${(v / 1000).toFixed(0)}K`
  return `${s}${v}`
}
