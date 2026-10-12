import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { AuthProvider } from "./hooks/useAuth";
import { LiveProvider } from "./hooks/useLive";
import { queryClient } from "./lib/queryClient";
import "./styles/global.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <LiveProvider>
          <App />
        </LiveProvider>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>
);
