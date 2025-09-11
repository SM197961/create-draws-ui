// vite.config.js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,               // bind to 0.0.0.0 so tunnels/proxies can reach it
    port: 5173,               // keep it stable
    strictPort: true,         // don't auto-switch to 5174
    allowedHosts: ["ctrlcap-ui.ngrok.app"],  // <-- your reserved UI domain
    hmr: {
      host: "ctrlcap-ui.ngrok.app",         // HMR over ngrok
      protocol: "wss",
      clientPort: 443,
    },
  },
});