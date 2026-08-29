import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const adminApiTarget =
    env.VITE_ADMIN_API_TARGET?.trim() || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": {
          target: adminApiTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
