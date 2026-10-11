import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
  },
  test: {
    environment: "jsdom",
    // e2e/ holds Playwright specs, run with `npm run e2e` against a live stack
    exclude: [...configDefaults.exclude, "e2e/**"],
    setupFiles: ["./src/test/setup.ts"],
    // Tests normally skip CSS. The contrast tests read the real design tokens out of global.css, so
    // let exactly that file through (imported with ?raw).
    css: { include: [/styles\/global\.css/] },
  },
});
