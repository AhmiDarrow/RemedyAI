/**
 * Isolated dogfood profile: run tauri:dev beside an installed release.
 *
 * Defaults:
 *   REMEDY_HOME      = %USERPROFILE%\.remedy-dev
 *   REMEDY_API_PORT  = 7410
 *   VITE_PORT        = 5174
 *   VITE_REMEDY_API  = http://127.0.0.1:7410
 *   REMEDY_PROFILE   = dev
 *   REMEDY_DEV_ROOT  = repo root (parent of desktop/)
 *
 * Release stays on :7400 + ~/.remedy. This never kills :7400.
 */
import { spawn } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const desktopRoot = path.resolve(__dirname, '..')
const repoRoot = path.resolve(desktopRoot, '..')

const apiPort = process.env.REMEDY_API_PORT || '7410'
const vitePort = process.env.VITE_PORT || '5174'
const home =
  process.env.REMEDY_HOME
  || path.join(os.homedir(), '.remedy-dev')

const env = {
  ...process.env,
  REMEDY_HOME: home,
  REMEDY_API_PORT: String(apiPort),
  REMEDY_PROFILE: process.env.REMEDY_PROFILE || 'dev',
  REMEDY_DEV_ROOT: process.env.REMEDY_DEV_ROOT || repoRoot,
  VITE_PORT: String(vitePort),
  VITE_REMEDY_API:
    process.env.VITE_REMEDY_API || `http://127.0.0.1:${apiPort}`,
}

// Merge tauri config: separate vite port + window title
const configMerge = {
  build: {
    devUrl: `http://localhost:${vitePort}`,
    beforeDevCommand: `npm run dev -- --port ${vitePort} --strictPort`,
  },
  app: {
    windows: [
      {
        title: `Remedy Desktop (dev · :${apiPort})`,
      },
    ],
    security: {
      // Allow connect to isolated API port (CSP is additive via merge).
      csp: `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ipc: http://ipc.localhost http://127.0.0.1:7400 http://localhost:7400 http://127.0.0.1:${apiPort} http://localhost:${apiPort} https: http:; img-src 'self' data: blob: asset: http://asset.localhost http://127.0.0.1:7400 http://localhost:7400 http://127.0.0.1:${apiPort} http://localhost:${apiPort} https: http:; font-src 'self' data:; frame-src https: http: data: blob:; child-src https: http: data: blob:; object-src 'none'; base-uri 'self'`,
    },
  },
}

console.log('[tauri:dev:isolated]')
console.log(`  REMEDY_HOME     = ${env.REMEDY_HOME}`)
console.log(`  REMEDY_API_PORT = ${env.REMEDY_API_PORT}`)
console.log(`  VITE_PORT       = ${env.VITE_PORT}`)
console.log(`  VITE_REMEDY_API = ${env.VITE_REMEDY_API}`)
console.log(`  REMEDY_DEV_ROOT = ${env.REMEDY_DEV_ROOT}`)
console.log('  Release can stay on :7400 + ~/.remedy')

const child = spawn(
  'npx',
  ['tauri', 'dev', '--config', JSON.stringify(configMerge)],
  {
    cwd: desktopRoot,
    env,
    stdio: 'inherit',
    shell: true,
  },
)

child.on('exit', (code, signal) => {
  if (signal) process.exit(1)
  process.exit(code ?? 0)
})
