import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const root = resolve(import.meta.dirname);

export default defineConfig({
  root: resolve(root, "web"),
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    https: {
      cert: readFileSync(resolve(root, "pem/cert.pem")),
      key: readFileSync(resolve(root, "pem/key.pem"))
    }
  },
  build: {
    outDir: resolve(root, "web/dist"),
    emptyOutDir: true
  }
});
