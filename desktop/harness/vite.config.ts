// Dev-only visual harness for the live chat turn. Not part of the app build.
//   npx vite --config harness/vite.config.ts
// then open http://127.0.0.1:5199/harness/live-turn/?t=9000 (ms into the scripted turn) or ?done=1
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  // Root is the desktop package so Tailwind scans src/ exactly like the app.
  root: fileURLToPath(new URL('..', import.meta.url)),
  plugins: [react(), tailwindcss()],
  server: { port: 5199, strictPort: true, host: '127.0.0.1' },
  logLevel: 'warn',
})
