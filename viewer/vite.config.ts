import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";

// pdf.js 静态资源（CMap / 标准字体）：
// CJK CID 字体解码依赖预定义 CMap——缺失时中文整页空白；
// 非嵌入字体（CMR10 等数学字体）依赖标准字体数据——缺失时根号等字形错位。
// 构建时复制进 dist（FastAPI 同源托管 /cmaps/、/standard_fonts/），
// 开发模式用中间件直接从 node_modules 提供。
function pdfjsAssets(): Plugin {
  const src = path.resolve(__dirname, "node_modules/pdfjs-dist");
  return {
    name: "pdfjs-assets",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const m = (req.url ?? "").match(/^\/(cmaps|standard_fonts)\/(.+)$/);
        if (!m) return next();
        const file = path.join(src, m[1], path.normalize(m[2]));
        if (!file.startsWith(src) || !fs.existsSync(file)) return next();
        res.setHeader("Content-Type", "application/octet-stream");
        fs.createReadStream(file).pipe(res);
      });
    },
    closeBundle() {
      for (const dir of ["cmaps", "standard_fonts"]) {
        fs.cpSync(path.join(src, dir), path.resolve(__dirname, "dist", dir),
                  { recursive: true });
      }
    },
  };
}

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
  plugins: [react(), pdfjsAssets()],
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
