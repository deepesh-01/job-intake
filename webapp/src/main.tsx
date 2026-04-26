import React from "react"
import ReactDOM from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Toaster } from "sonner"
import { App } from "./App"
import "./styles.css"

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <App />
      <Toaster
        position="bottom-right"
        theme="dark"
        toastOptions={{
          style: {
            background: "hsl(240 10% 5%)",
            border: "1px solid hsl(240 3.7% 15.9%)",
            color: "hsl(0 0% 98%)",
          },
        }}
      />
    </QueryClientProvider>
  </React.StrictMode>,
)
