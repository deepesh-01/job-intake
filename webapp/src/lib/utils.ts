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
