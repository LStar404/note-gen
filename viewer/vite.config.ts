import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";

// 开发模式：仅绑定 loopback，经 /api 代理注入本地 token（规范 10.6.5）
function readLocalToken(): string {
  const candidates = [
    path.resolve(__dirname, "../data/.local_token"),
    path.resolve(process.cwd(), "../data/.local_token"),
    path.resolve(process.cwd(), "data/.local_token"),
  ];
  for (const p of candidates) {
    try {
      return fs.readFileSync(p, "utf-8").trim();
    } catch {
      /* 继续尝试下一个候选路径 */
    }
  }
  return "";
}

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: true,
        configure: (proxy) => {
          const token = readLocalToken();
          proxy.on("proxyReq", (proxyReq) => {
            if (token) proxyReq.setHeader("X-Local-Token", token);
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
