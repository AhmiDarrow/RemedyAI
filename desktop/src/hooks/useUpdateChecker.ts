import { useState, useCallback, useEffect, useRef } from 'react'
import {
  checkDesktopUpdate,
  checkUpdates,
  isNewerVersion,
  type DesktopUpdateInfo,
  type UpdateInfo,
} from '../api/updates'
import { isTauri } from '../api/tauri'

const CHECK_INTERVAL = 30 * 60 * 1000 // 30 minutes

export type UpdateCheckResult = {
  updateInfo: UpdateInfo
  desktopInfo: DesktopUpdateInfo | null
  updateAvailable: boolean
}

function mergeUpdateSources(
  shellCurrent: string,
  desk: DesktopUpdateInfo | null,
  api: UpdateInfo | null,
): UpdateCheckResult {
  const current =
    shellCurrent && shellCurrent !== 'unknown'
      ? shellCurrent.replace(/^[vV]/, '')
      : (desk?.current_version || api?.current_version || 'unknown').replace(/^[vV]/, '')

  const latestDesktop =
    (desk?.latest_version && desk.latest_version !== 'unknown'
      ? desk.latest_version
      : null) ||
    api?.latest_desktop ||
    null
  const latestPython = api?.latest_python || null

  // Prefer the higher of desktop release vs PyPI when both exist.
  let latest = latestDesktop || latestPython || current
  if (latestDesktop && latestPython) {
    latest = isNewerVersion(latestDesktop, latestPython) ? latestDesktop : latestPython
  }
  latest = String(latest).replace(/^[vV]/, '')

  const download =
    desk?.download_url ||
    api?.installer_url ||
    null
  const releaseUrl =
    api?.release_url ||
    (download
      ? 'https://github.com/AhmiDarrow/RemedyAI/releases/latest'
      : null)

  const newer = isNewerVersion(latest, current)
  const installable = newer && Boolean(download && String(download).trim())

  const errors = [desk?.error, api?.error].filter(Boolean) as string[]
  if (newer && !installable) {
    errors.push(
      'A newer version exists but no Windows installer URL was found on the release.',
    )
  }

  const updateInfo: UpdateInfo = {
    current_version: current,
    latest_python: latestPython,
    latest_desktop: latestDesktop || (latest !== current ? latest : latestDesktop),
    release_url: releaseUrl,
    installer_url: download,
    // True when an in-app install can proceed (has installer URL).
    update_available: installable,
    error: errors.length ? errors.join(' · ') : null,
    python_version: api?.python_version ?? null,
  }

  const desktopInfo: DesktopUpdateInfo = {
    current_version: current,
    latest_version: latest,
    update_available: installable,
    download_url: download,
    release_notes: desk?.release_notes || null,
    error: updateInfo.error,
  }

  return {
    updateInfo,
    desktopInfo,
    updateAvailable: installable,
  }
}

/**
 * @param ready When false, defers the launch check (e.g. Tauri until sidecar is up).
 *   Periodic 30m checks still only run while ready. Manual `check()` always works.
 */
export function useUpdateChecker(options?: { ready?: boolean }) {
  const ready = options?.ready !== false
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null)
  const [desktopInfo, setDesktopInfo] = useState<DesktopUpdateInfo | null>(null)
  const [checking, setChecking] = useState(false)
  const [lastCheckedAt, setLastCheckedAt] = useState<number | null>(null)
  const [lastStatus, setLastStatus] = useState<string | null>(null)
  const intervalRef = useRef<number | null>(null)
  const inFlightRef = useRef(false)
  /** One automatic check per app session after launch/ready (not per remount). */
  const didLaunchCheckRef = useRef(false)

  const check = useCallback(async (): Promise<UpdateCheckResult> => {
    if (inFlightRef.current) {
      return {
        updateInfo: updateInfo || {
          current_version: 'unknown',
          latest_python: null,
          latest_desktop: null,
          release_url: null,
          installer_url: null,
          update_available: false,
          error: null,
        },
        desktopInfo,
        updateAvailable: Boolean(desktopInfo?.update_available),
      }
    }
    inFlightRef.current = true
    setChecking(true)
    setLastStatus('Checking GitHub releases…')
    try {
      let desk: DesktopUpdateInfo | null = null
      let api: UpdateInfo | null = null
      let shellCurrent = 'unknown'

      if (isTauri()) {
        desk = await checkDesktopUpdate()
        shellCurrent =
          desk.current_version && desk.current_version !== 'unknown'
            ? desk.current_version
            : 'unknown'
      }

      // Always hit the API as a second source, keyed by *shell* version so a
      // rebuilt sidecar cannot claim the app is already on the latest build.
      try {
        api = await checkUpdates(
          shellCurrent !== 'unknown' ? shellCurrent : undefined,
        )
        if (shellCurrent === 'unknown' && api.current_version) {
          shellCurrent = api.current_version
        }
      } catch (apiErr) {
        const msg = apiErr instanceof Error ? apiErr.message : String(apiErr)
        if (!desk) {
          throw new Error(msg)
        }
        api = {
          current_version: shellCurrent,
          latest_python: null,
          latest_desktop: desk.latest_version,
          release_url: null,
          installer_url: desk.download_url,
          update_available: desk.update_available,
          error: `API fallback failed: ${msg}`,
        }
      }

      const merged = mergeUpdateSources(shellCurrent, desk, api)
      setUpdateInfo(merged.updateInfo)
      setDesktopInfo(merged.desktopInfo)
      setLastCheckedAt(Date.now())
      if (merged.updateAvailable) {
        setLastStatus(
          `Update available: v${merged.updateInfo.current_version} → v${merged.desktopInfo?.latest_version || merged.updateInfo.latest_desktop}`,
        )
      } else if (merged.updateInfo.error && !merged.updateInfo.latest_desktop) {
        setLastStatus(merged.updateInfo.error)
      } else {
        const lat = merged.updateInfo.latest_desktop || merged.desktopInfo?.latest_version
        setLastStatus(
          lat
            ? `Up to date — v${merged.updateInfo.current_version} (latest v${lat})`
            : `Up to date — v${merged.updateInfo.current_version}`,
        )
      }
      return merged
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      const failed: UpdateInfo = {
        current_version: 'unknown',
        latest_python: null,
        latest_desktop: null,
        release_url: null,
        installer_url: null,
        update_available: false,
        error: `Update check failed: ${msg}`,
      }
      setUpdateInfo(failed)
      setDesktopInfo(null)
      setLastCheckedAt(Date.now())
      setLastStatus(failed.error)
      return {
        updateInfo: failed,
        desktopInfo: null,
        updateAvailable: false,
      }
    } finally {
      inFlightRef.current = false
      setChecking(false)
    }
    // Intentionally stable: uses functional state setters; inFlight ref guards races.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    // Wait until the shell is ready (sidecar up) so launch check is real, not a
    // silent miss during "connecting". Was 25s from mount — felt like "never".
    if (!ready) return

    // One check soon after launch/ready. Manual "Check for updates" still works anytime.
    let launchTimer: number | null = null
    if (!didLaunchCheckRef.current) {
      didLaunchCheckRef.current = true
      // Short settle so settings/sessions bind first; still clearly "on launch".
      launchTimer = window.setTimeout(() => {
        void check()
      }, 2_000)
    }

    intervalRef.current = window.setInterval(() => {
      void check()
    }, CHECK_INTERVAL)
    return () => {
      if (launchTimer != null) window.clearTimeout(launchTimer)
      if (intervalRef.current != null) {
        window.clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [check, ready])

  return {
    updateInfo,
    desktopInfo,
    checking,
    lastCheckedAt,
    lastStatus,
    check,
    updateAvailable: Boolean(
      desktopInfo?.update_available || updateInfo?.update_available,
    ),
  }
}
