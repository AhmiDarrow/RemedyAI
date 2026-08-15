# Work area of the *monitor this Remedy window is on* (3-monitor safe).
# Env:
#   REMEDY_HINT_RECT  = x,y,w,h  current window (pick the matching HWND / screen)
#   REMEDY_PLACE_HOST = x,y,w,h  MoveWindow that HWND onto the work area
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class Wa {
  [DllImport("user32.dll")] public static extern bool EnumWindows(Cb cb, IntPtr lp);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R r);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out R r);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int ht, bool rep);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public delegate bool Cb(IntPtr h, IntPtr lp);
  public struct R { public int L; public int T; public int Rgt; public int B; }
}
"@
Add-Type -AssemblyName System.Windows.Forms

$hintX = $null; $hintY = $null; $hintW = $null; $hintH = $null
if ($env:REMEDY_HINT_RECT -match '^(-?\d+),(-?\d+),(\d+),(\d+)$') {
  $hintX = [int]$Matches[1]; $hintY = [int]$Matches[2]
  $hintW = [int]$Matches[3]; $hintH = [int]$Matches[4]
}

$cands = New-Object System.Collections.Generic.List[object]
$cb = [Wa+Cb]{
  param($h, $lp)
  if (-not [Wa]::IsWindowVisible($h)) { return $true }
  $sb = New-Object System.Text.StringBuilder 256
  [void][Wa]::GetWindowText($h, $sb, $sb.Capacity)
  $title = $sb.ToString()
  if (-not $title) { return $true }
  $isRemedy = $title -like '*Remedy Desktop*' -or $title -like '*Remedy*'
  $isWsl = $title -like '*Ubuntu*' -or $title -like '*WSL*' -or $title -like '*COPY MODE*'
  if (-not ($isRemedy -or $isWsl)) { return $true }
  $r = New-Object Wa+R
  [void][Wa]::GetWindowRect($h, [ref]$r)
  $ww = [Math]::Max(1, $r.Rgt - $r.L)
  $wh = [Math]::Max(1, $r.B - $r.T)
  if ($ww -lt 200 -or $wh -lt 200) { return $true }
  $score = 0
  if ($title -like '*Remedy Desktop*') { $score += 250 }
  elseif ($isRemedy) { $score += 40 }
  if ($isWsl -and $isRemedy) { $score += 40 }
  elseif ($isWsl) { $score += 10 }
  if ($null -ne $hintX) {
    $cx = $r.L + [int]($ww / 2)
    $cy = $r.T + [int]($wh / 2)
    $hx = $hintX + [int]($hintW / 2)
    $hy = $hintY + [int]($hintH / 2)
    $dist = [Math]::Abs($cx - $hx) + [Math]::Abs($cy - $hy)
    $score += [Math]::Max(0, 400 - [int]($dist / 8))
  }
  $cands.Add([pscustomobject]@{ H = $h; R = $r; Title = $title; Score = $score; W = $ww; Ht = $wh })
  return $true
}
[Wa]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null

$pick = $null
if ($cands.Count -gt 0) {
  $pick = $cands | Sort-Object -Property Score -Descending | Select-Object -First 1
}

$screen = $null
if ($pick) {
  $wr = New-Object System.Drawing.Rectangle $pick.R.L, $pick.R.T, $pick.W, $pick.Ht
  $screen = [System.Windows.Forms.Screen]::FromRectangle($wr)
} elseif ($null -ne $hintX) {
  $pt = New-Object System.Drawing.Point ($hintX + [int]($hintW / 2)), ($hintY + [int]($hintH / 2))
  $screen = [System.Windows.Forms.Screen]::FromPoint($pt)
} else {
  $screen = [System.Windows.Forms.Screen]::FromPoint([System.Windows.Forms.Cursor]::Position)
}
if (-not $screen) { $screen = [System.Windows.Forms.Screen]::PrimaryScreen }

$w = $screen.WorkingArea
Write-Output ("work={0},{1},{2},{3}" -f $w.X, $w.Y, $w.Width, $w.Height)
if ($pick) {
  Write-Output ("win={0},{1},{2},{3}" -f $pick.R.L, $pick.R.T, $pick.W, $pick.Ht)
  Write-Output ("title={0}" -f $pick.Title)
}

$place = $env:REMEDY_PLACE_HOST
if ($pick -and $place -and $place -match '^(-?\d+),(-?\d+),(\d+),(\d+)$') {
  [void][Wa]::ShowWindow($pick.H, 9)
  Start-Sleep -Milliseconds 40
  [void][Wa]::MoveWindow($pick.H, [int]$Matches[1], [int]$Matches[2], [int]$Matches[3], [int]$Matches[4], $true)
}
