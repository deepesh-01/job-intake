const SOURCE_LABEL: Record<string, string> = {
  instahyre:    "Instahyre",
  hirist:       "Hirist",
  naukri:       "Naukri",
  workday:      "Workday",
  linkedin:     "LinkedIn",
  linkedin_auth: "LinkedIn",
  greenhouse:   "Greenhouse",
  ashby:        "Ashby",
  lever:        "Lever",
  remoteok:     "RemoteOK",
  remotive:     "Remotive",
  arbeitnow:    "Arbeitnow",
  hn_hiring:    "HN",
  yc_waas:      "YC",
  hasjob:       "Hasjob",
}

export function sourceFromId(id: string): string {
  const prefix = id.split(":", 1)[0]
  return SOURCE_LABEL[prefix] ?? prefix
}
