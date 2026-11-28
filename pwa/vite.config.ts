import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig(({ mode }) => {
  const localStudio = mode === "localstudio";
  const base = localStudio ? "/" : process.env.VITE_BASE || "/";
  const withSlash = base.endsWith("/") ? base : `${base}/`;

  return {
    base: withSlash,
    define: localStudio ? { "import.meta.env.VITE_API_BASE": JSON.stringify("") } : undefined,
    plugins: [
      react(),
      tailwindcss(),
      VitePWA({
        registerType: "autoUpdate",
        includeAssets: ["icon-192.png", "icon-512.png", "icon-maskable-512.png"],
        manifest: {
          name: "Agent Cockpit",
          short_name: "Cockpit",
          description: "Mobile control center for an autonomous AI software engineer",
          theme_color: "#0a0a0a",
          background_color: "#0a0a0a",
          display: "standalone",
          start_url: withSlash,
          scope: withSlash,
          icons: [
            { src: "icon-192.png", sizes: "192x192", type: "image/png" },
            { src: "icon-512.png", sizes: "512x512", type: "image/png" },
            {
              src: "icon-maskable-512.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "maskable",
            },
          ],
        },
        workbox: {
          importScripts: ["push-handler.js"],
          navigateFallback: `${withSlash}index.html`,
          navigateFallbackDenylist: [/^\/api(?:\/|$)/, /^\/s\//],
          globPatterns: ["**/*.{js,css,html,png,svg}"],
        },
      }),
    ],
    server: {
      host: true,
      port: 5173,
      proxy: {
        "/api": { target: "http://localhost:8000", changeOrigin: true },
      },
    },
  };
});
