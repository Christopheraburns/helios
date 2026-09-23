import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const buildNumber =
  process.env.HELIOS_UI_BUILD_NUMBER?.trim() ||
  new Date().toISOString().replace(/\D/g, "").slice(0, 14);

export default defineConfig({
  plugins: [react()],
  define: {
    __HELIOS_BUILD_NUMBER__: JSON.stringify(buildNumber),
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
