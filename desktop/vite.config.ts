import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Isolated dogfood: VITE_PORT=5174 VITE_REMEDY_API=http://127.0.0.1:7410
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiOrigin = (env.VITE_REMEDY_API || process.env.VITE_REMEDY_API || 'http://127.0.0.1:7400')
    .replace(/\/$/, '')
    .replace(/\/api$/i, '')
  const port = Number(env.VITE_PORT || process.env.VITE_PORT || 5173) || 5173

  return {
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
      port,
      strictPort: true,
      // Tauri expects the dev server on localhost; bind explicitly for Windows.
      host: 'localhost',
      proxy: {
        '/api': apiOrigin,
      },
    },
  }
})
