import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/generate": "http://localhost:8765",
      "/task": "http://localhost:8765"
    }
  }
});
