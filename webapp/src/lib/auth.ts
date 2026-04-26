import { useQuery } from "@tanstack/react-query"
import { fetchHealth, getWriteToken } from "./api"

export interface AuthState {
  isReady: boolean       // health check done
  serverLocked: boolean  // server requires a token to mutate
  hasToken: boolean      // localStorage has a token
  canMutate: boolean     // !serverLocked || hasToken
}

/** Single source of truth for "is this browser allowed to mutate?".
 *  Combines the server's read_only flag (does it require a token at all?)
 *  with whether localStorage holds one. */
export function useAuth(): AuthState {
  const { data, isLoading } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })
  const serverLocked = data?.read_only ?? false
  const hasToken = getWriteToken().length > 0
  return {
    isReady: !isLoading,
    serverLocked,
    hasToken,
    canMutate: !serverLocked || hasToken,
  }
}
