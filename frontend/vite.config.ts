import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// The slide artwork is served by the API from the repository's assets/
// directory rather than copied into public/: 12MB of PNGs in two places drift,
// and the copy is the one that goes stale. It is proxied at /media, because
// /assets is where Vite puts the built bundle.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/media": "http://127.0.0.1:8000",
      "/auth": "http://127.0.0.1:8000",
    },
  },
  build: { outDir: "../server/spa", emptyOutDir: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
})
