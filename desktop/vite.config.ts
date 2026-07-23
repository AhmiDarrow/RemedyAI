import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'remove-crossorigin',
      transformIndexHtml(html) {
        return html.replace(/\s+crossorigin(?:="[^"]*")?/g, '')
      },
    },
  ],
  base: '',
  // clearScreen: false keeps Vite logs visible under `tauri dev`
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    // Tauri expects the dev server on localhost; bind explicitly for Windows.
    host: 'localhost',
    proxy: {
      '/api': 'http://127.0.0.1:7400',
    },
  },
})
