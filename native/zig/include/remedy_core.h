#ifndef REMEDY_CORE_H
#define REMEDY_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * ABI 5 adds ConPTY (CreatePseudoConsole spawn/read/write/poll/kill/close)
 * on top of ABI 4 host_op_prepare / translate_posix_to_host, ABI 3 UI
 * Automation / Linux AT-SPI, and the ABI 2 host surface (DPI, monitors,
 * capture, PNG, input, windows, clipboard and hidden process control).
 * Additive on the same ABI 5: authorized spawn (policy + HMAC capability
 * tokens), signing-key set/clear, argv hash, token issue, authorized
 * one-shot exec capture (stdout/stderr + timeout/kill-tree), authorized
 * interactive 3-pipe spawn (separate stdin/stdout/stderr OS handles for
 * Python workers), write-jail / workdir roots (set/clear/check), HostSession
 * orchestration (open/run/cwd/close + wrap/split protocol), host
 * diagnose/dialect/stretch, looks_like_powershell / rewrite_posix_argv
 * (argv-head twin of translate), CF_HDROP file-list clipboard read,
 * CF_DIB→PNG clipboard read, and foreground detail (hwnd/title/pid/exe).
 * Production Python spawn/session paths use the authorized exports; the
 * unsigned process_spawn_hidden / conpty_spawn / host_session_open symbols
 * remain for low-level tests.
 * Every host/UIA/ConPTY/policy function returns a remedy_core_status. On a
 * non-Windows build each host/UIA/ConPTY spawn function returns
 * REMEDY_CORE_UNSUPPORTED and writes nothing; host_op_prepare,
 * translate_posix_to_host, and the portable security helpers remain available.
 *
 * Memory: any buffer returned through an `out_*` pointer is owned by the
 * caller and must be released with remedy_core_free(ptr, len). Strings that
 * cross this boundary are UTF-8 (WTF-8 when a Win32 string carried a lone
 * surrogate). Handles (HWND, ConPTY session) travel as uint64_t.
 *
 * Errors: when a call fails with REMEDY_CORE_OPERATION_FAILED the Win32 error
 * code is available from remedy_core_last_os_error() on the same thread.
 * Policy / token failures return REMEDY_CORE_ACCESS_DENIED.
 */
#define REMEDY_CORE_ABI_VERSION 7u

enum remedy_core_status {
    REMEDY_CORE_OK = 0,
    REMEDY_CORE_INVALID_ARGUMENT = 1,
    REMEDY_CORE_ACCESS_DENIED = 2,
    REMEDY_CORE_OPERATION_FAILED = 3,
    REMEDY_CORE_UNSUPPORTED = 4,
};

enum remedy_core_capability {
    REMEDY_CORE_FILESYSTEM_READ = UINT64_C(1) << 0,
    REMEDY_CORE_FILESYSTEM_WRITE = UINT64_C(1) << 1,
    REMEDY_CORE_PROCESS_SPAWN = UINT64_C(1) << 2,
    REMEDY_CORE_SYSTEM_READ = UINT64_C(1) << 3,
    REMEDY_CORE_FILESYSTEM_DELETE = UINT64_C(1) << 4,
    REMEDY_CORE_OWNER_CHECKPOINT = UINT64_C(1) << 5,
};

/* Mouse buttons for remedy_core_mouse_click / remedy_core_mouse_button. */
enum remedy_core_mouse_button {
    REMEDY_CORE_MOUSE_LEFT = 0,
    REMEDY_CORE_MOUSE_RIGHT = 1,
    REMEDY_CORE_MOUSE_MIDDLE = 2,
};

/* Verbs for remedy_core_manage_window. */
enum remedy_core_window_action {
    REMEDY_CORE_WINDOW_MINIMIZE = 0,
    REMEDY_CORE_WINDOW_MAXIMIZE = 1,
    REMEDY_CORE_WINDOW_RESTORE = 2,
    REMEDY_CORE_WINDOW_CLOSE = 3,       /* posts WM_CLOSE; the app may prompt */
    REMEDY_CORE_WINDOW_MOVE_RESIZE = 4, /* SetWindowPos(x, y, width, height) */
};

/* ---- ABI 1 ------------------------------------------------------------- */

uint32_t remedy_core_abi_version(void);
uint8_t remedy_core_validate_frame(const uint8_t *ptr, size_t len);

int32_t remedy_core_file_size(
    uint64_t capability_bits,
    const uint8_t *root_ptr,
    size_t root_len,
    const uint8_t *path_ptr,
    size_t path_len,
    uint64_t *out_size
);

int32_t remedy_core_logical_cpu_count(
    uint64_t capability_bits,
    size_t *out_count
);

/* ---- ABI 2: memory and errors ----------------------------------------- */

/* Release a buffer returned by any function below. (NULL, 0) is a no-op. */
void remedy_core_free(uint8_t *ptr, size_t len);

/* Win32 error code recorded by the last failing host call on this thread. */
uint32_t remedy_core_last_os_error(void);

/* ---- ABI 2: DPI and monitors ------------------------------------------ */

/* Make the process Per-Monitor-DPI-aware (v2, then v1, then system) once.
 * Every capture and coordinate function calls this itself. */
int32_t remedy_core_dpi_awareness_enable(void);

/* Virtual screen origin and size in physical pixels. */
int32_t remedy_core_virtual_screen_rect(
    int32_t *out_left, int32_t *out_top, int32_t *out_width, int32_t *out_height
);

/* UTF-8 JSON array of
 * {index, left, top, right, bottom, width, height, primary, scale}
 * where scale is the effective DPI / 96 (1.0 at 100%). */
int32_t remedy_core_list_monitors(uint8_t **out_json, size_t *out_len);

/* ---- ABI 2: capture ---------------------------------------------------- */

/* bytes_per_pixel selects the DIB layout: 3 = BGR rows padded to 4 bytes,
 * 4 = BGRA with stride == width * 4. Rows run top-down. */
int32_t remedy_core_capture_virtual_screen(
    uint32_t bytes_per_pixel,
    uint8_t **out_pixels, size_t *out_len,
    int32_t *out_width, int32_t *out_height, size_t *out_stride,
    int32_t *out_left, int32_t *out_top
);

/* Capture a rectangle in screen coordinates (no clamping: the caller keeps
 * the rectangle inside the virtual screen). */
int32_t remedy_core_capture_region(
    int32_t left, int32_t top, int32_t width, int32_t height,
    uint32_t bytes_per_pixel,
    uint8_t **out_pixels, size_t *out_len, size_t *out_stride
);

/* PrintWindow(PW_RENDERFULLCONTENT), falling back to PrintWindow(0). Works
 * for occluded windows. Fails with INVALID_ARGUMENT for windows under 2x2. */
int32_t remedy_core_print_window(
    uint64_t hwnd,
    uint32_t bytes_per_pixel,
    uint8_t **out_pixels, size_t *out_len,
    int32_t *out_width, int32_t *out_height, size_t *out_stride,
    int32_t *out_left, int32_t *out_top
);

/* Encode BGR (3) or BGRA (4, alpha dropped) rows into an 8-bit RGB PNG.
 * Portable: also available on non-Windows builds. */
int32_t remedy_core_encode_png(
    const uint8_t *pixels, size_t pixels_len,
    int32_t width, int32_t height, size_t stride, uint32_t bytes_per_pixel,
    uint8_t **out_png, size_t *out_len
);

/* ---- ABI 2: input (SendInput) ----------------------------------------- */

/* Coordinates are virtual-screen physical pixels, normalised to 0..65535
 * with MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK. */
int32_t remedy_core_mouse_move(int32_t x, int32_t y);

/* Move, wait 20 ms, then `clicks` press/release pairs 40 ms apart. */
int32_t remedy_core_mouse_click(int32_t x, int32_t y, uint32_t button, uint32_t clicks);

/* Raw press (pressed != 0) or release of one button at the current position. */
int32_t remedy_core_mouse_button(uint32_t button, uint8_t pressed);

/* Press at (x1, y1), move through `steps` interpolated points, pause, release. */
int32_t remedy_core_mouse_drag(int32_t x1, int32_t y1, int32_t x2, int32_t y2, uint32_t steps);

/* Wheel notches: dy > 0 scrolls up, dx > 0 scrolls right (120 units each). */
int32_t remedy_core_mouse_scroll(int32_t x, int32_t y, int32_t dx, int32_t dy);

/* Type UTF-8 text with KEYEVENTF_UNICODE (surrogate pairs for astral code
 * points). "\r\n", "\n" and "\r" each press VK_RETURN once. Sleeps
 * per_char_delay_ms after every code point. */
int32_t remedy_core_type_text(const uint8_t *utf8, size_t len, uint32_t per_char_delay_ms);

/* Press virtual keys in order, release them in reverse order. */
int32_t remedy_core_key_combo(const uint16_t *vks, size_t count);

/* Press one virtual key, hold for hold_ms, release. */
int32_t remedy_core_key_hold(uint16_t vk, uint32_t hold_ms);

/* VkKeyScanW for a code point: low byte VK, high byte shift state, -1 when
 * the current layout has no mapping. Code points above U+FFFF yield -1. */
int32_t remedy_core_vk_key_scan(uint32_t codepoint, int32_t *out_scan);

/* ---- ABI 2: windows ---------------------------------------------------- */

/* UTF-8 JSON array (at most `limit` entries) of visible, titled windows of
 * at least 8x8 pixels:
 * {hwnd, title (<= 200 code points), class, pid,
 *  bounds: {left, top, right, bottom}, width, height, visible, minimized} */
int32_t remedy_core_list_windows(uint32_t limit, uint8_t **out_json, size_t *out_len);

/* Window class name. */
int32_t remedy_core_window_class(uint64_t hwnd, uint8_t **out_utf8, size_t *out_len);

/* GetWindowRect. */
int32_t remedy_core_window_rect(
    uint64_t hwnd, int32_t *out_left, int32_t *out_top, int32_t *out_right, int32_t *out_bottom
);

/* Foreground window handle (0 when none) and its full title. */
int32_t remedy_core_foreground_window(uint64_t *out_hwnd, uint8_t **out_title, size_t *out_len);

/* Restore + SetForegroundWindow, verified; on a foreground lock retries via
 * AttachThreadInput and then an ALT tap. out_focused is 1 when the window
 * (or a window sharing its root owner) is foreground afterwards. */
int32_t remedy_core_focus_window(uint64_t hwnd, uint8_t *out_focused);

/* See remedy_core_window_action. x, y, width, height are used only by
 * REMEDY_CORE_WINDOW_MOVE_RESIZE. INVALID_ARGUMENT for a dead handle. */
int32_t remedy_core_manage_window(
    uint64_t hwnd, uint32_t action, int32_t x, int32_t y, int32_t width, int32_t height
);

/* First descendant of `parent` whose class contains class_substr and whose
 * title contains title_substr (ASCII case-insensitive; an empty substring
 * matches everything). out_hwnd is 0 when nothing matched. */
int32_t remedy_core_find_child_hwnd(
    uint64_t parent,
    const uint8_t *class_substr, size_t class_len,
    const uint8_t *title_substr, size_t title_len,
    uint64_t *out_hwnd
);

/* ---- ABI 2: clipboard -------------------------------------------------- */

/* CF_UNICODETEXT as UTF-8; an empty buffer when the clipboard holds no text.
 * Retries OpenClipboard five times, 20 ms apart, then fails. */
int32_t remedy_core_clipboard_get_text(uint8_t **out_utf8, size_t *out_len);

/* Replace the clipboard contents with UTF-8 text (same retry policy). */
int32_t remedy_core_clipboard_set_text(const uint8_t *utf8, size_t len);

/* CF_HDROP paths as a JSON string array. Empty `[]` when the format is
 * absent. Windows only; unsupported elsewhere. */
int32_t remedy_core_clipboard_get_files(uint8_t **out_json, size_t *out_len);

/* CF_DIB encoded as PNG. Empty buffer when absent or the DIB is not
 * BI_RGB 24/32-bit. Windows only; unsupported elsewhere. */
int32_t remedy_core_clipboard_get_image_png(uint8_t **out_png, size_t *out_len);

/* JSON object `{hwnd,title,pid,exe}` for the foreground window (empty
 * fields when none). Windows + Linux/X11; unsupported elsewhere. */
int32_t remedy_core_foreground_detail(uint8_t **out_json, size_t *out_len);

/* ---- ABI 2: processes -------------------------------------------------- */

/* Start argv_json (a JSON array of strings; argv[0] must be absolute on
 * Linux; on Windows CreateProcessW resolves like subprocess.Popen) hidden
 * with no shared stdio. Windows: CREATE_NO_WINDOW + job object with
 * JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. Linux: own process group (pgid=0);
 * process_close / kill_tree ends the group. cwd may be empty (inherit).
 * env_json is a JSON object of strings replacing the whole environment, or
 * empty to inherit. */
int32_t remedy_core_process_spawn_hidden(
    const uint8_t *argv_json, size_t argv_len,
    const uint8_t *cwd, size_t cwd_len,
    const uint8_t *env_json, size_t env_len,
    uint32_t *out_pid, uint64_t *out_handle
);

/* Wait up to timeout_ms (UINT32_MAX = forever). out_exited is 1 with the
 * exit code when the process ended, 0 when the timeout elapsed. */
int32_t remedy_core_process_wait(
    uint64_t handle, uint32_t timeout_ms, uint8_t *out_exited, uint32_t *out_exit_code
);

/* Terminate pid and every descendant. Windows: toolhelp snapshot (deepest
 * first). Linux: process-group SIGKILL + /proc ppid walk. pid 0/1 invalid.
 * A pid that is already gone is OK. */
int32_t remedy_core_process_kill_tree(uint32_t pid);

/* Close the spawn handle. Windows: closing the job kills remaining children.
 * Linux: SIGKILL the process group then reap. Frees the handle; do not reuse. */
int32_t remedy_core_process_close(uint64_t handle);

/* ---- ABI 3: UI Automation --------------------------------------------- */

/* 1 when CoCreateInstance(CUIAutomation) succeeds on this thread; 0 otherwise.
 * Does not walk the tree. Non-Windows returns UNSUPPORTED with *out=0. */
int32_t remedy_core_uia_available(uint8_t *out_available);

/* UTF-8 JSON array of control dicts (ref/tag/role/name/x/y/w/h/hwnd/bounds/uia
 * and optional offscreen), or the JSON literal null when UIA is unavailable
 * or the walk found nothing. hwnd 0 = desktop root (named top-level windows).
 * max_elements 0 defaults to 80; clamped to [1, 120]. */
int32_t remedy_core_uia_control_snapshot(
    uint64_t hwnd,
    uint32_t max_elements,
    uint8_t preferred_only,
    uint8_t **out_json,
    size_t *out_len
);

/* UTF-8 JSON object {title, text, fields:[{name,role,value}]} or null. */
int32_t remedy_core_uia_read_window_text(
    uint64_t hwnd,
    uint32_t max_chars,
    uint8_t **out_json,
    size_t *out_len
);

/* UTF-8 JSON object {name, role, value} for the focused element, or null. */
int32_t remedy_core_uia_focused_element(uint8_t **out_json, size_t *out_len);

/* UTF-8 JSON object {ok, message, verified?}. Always an object (never null).
 * action is invoke | set_value | toggle | scroll_into_view. */
int32_t remedy_core_uia_element_action(
    uint64_t hwnd,
    const uint8_t *name, size_t name_len,
    const uint8_t *role, size_t role_len,
    const uint8_t *action, size_t action_len,
    const uint8_t *text, size_t text_len,
    uint8_t **out_json,
    size_t *out_len
);

/* ---- ABI 3: Linux accessibility (AT-SPI) -------------------------------- */

/* UTF-8 JSON array of clickable AT-SPI candidates
 * {x,y,w,h,area,name,role,source:"atspi"} (centers in screen pixels).
 * Linux only; other platforms return UNSUPPORTED. Empty array when the
 * desktop root is missing. No invoke / set_value / toggle (parity with the
 * former Python walker). */
int32_t remedy_core_a11y_snapshot(
    uint32_t limit,
    uint8_t **out_json,
    size_t *out_len
);

/* ---- ABI 4: Host Command IR prepare ------------------------------------- */

/* Prepare a HostOp or command string into a PreparedCommand. json_in is UTF-8
 * JSON: a bare HostOp ({kind,...}), {op:<HostOp>, scratch_dir?, project_path?},
 * or {command:"...", host?, scratch_dir?, project_path?} for prepare_host_command.
 * On success *out_json / *out_len hold UTF-8 PreparedCommand JSON
 * {argv,display,kind,ir,host,script_path?,notes?,translated?} — caller frees
 * with remedy_core_free. Supports run|script|mkdir|which|env|chain|raw. */
int32_t remedy_core_host_op_prepare(
    const uint8_t *json_in,
    size_t json_in_len,
    uint8_t **out_json,
    size_t *out_len
);

/* POSIX→host command-string rewrite (translate_posix_to_host). json_in is
 * {"command":"...","host":"cmd"|"posix"|omit,"rg_path"?: "...",
 *  "python_exe"?: "...","pwsh_exe"?: "..."}. On success *out_json holds
 * {"text","changed","notes","untranslatable","noop"} — caller frees with
 * remedy_core_free. */
int32_t remedy_core_translate_posix_to_host(
    const uint8_t *json_in,
    size_t json_in_len,
    uint8_t **out_json,
    size_t *out_len
);

/* 1 when command looks like PowerShell (not POSIX/cmd or a script name);
 * 0 otherwise. Empty command → 0. Always OK when out_flag is non-null. */
int32_t remedy_core_looks_like_powershell(
    const uint8_t *command, size_t command_len,
    uint8_t *out_flag
);

/* Rewrite a few POSIX argv heads (wc -l) for host_run. json_in is
 * {"argv":[...],"python_exe"?: "...","pwsh_exe"?: "..."}. On success
 * *out_json holds {"argv":[...],"notes":[...]} — caller frees. */
int32_t remedy_core_rewrite_posix_argv(
    const uint8_t *json_in,
    size_t json_in_len,
    uint8_t **out_json,
    size_t *out_len
);

/* ---- ABI 5: ConPTY -------------------------------------------------------- */

/* 1 when CreatePseudoConsole is available on this process; 0 otherwise.
 * Non-Windows returns UNSUPPORTED with *out_available = 0. */
int32_t remedy_core_conpty_available(uint8_t *out_available);

/* Spawn argv_json (JSON string array) attached to a ConPTY. cols/rows of 0
 * default to 120x40. cwd may be empty (inherit). env_json is a JSON object of
 * strings replacing the environment, or empty to inherit. On success
 * *out_handle is an opaque session; close with remedy_core_conpty_close. */
int32_t remedy_core_conpty_spawn(
    const uint8_t *argv_json, size_t argv_len,
    const uint8_t *cwd, size_t cwd_len,
    const uint8_t *env_json, size_t env_len,
    uint16_t cols, uint16_t rows,
    uint32_t *out_pid, uint64_t *out_handle
);

/* Write bytes to the session's stdin pipe. */
int32_t remedy_core_conpty_write(
    uint64_t handle, const uint8_t *data, size_t len, size_t *out_written
);

/* Read up to max_len into caller-owned buf. *out_len is 0 at EOF / empty. */
int32_t remedy_core_conpty_read(
    uint64_t handle, uint8_t *buf, size_t max_len, size_t *out_len
);

/* *out_exited is 0 while STILL_ACTIVE; else 1 with *out_exit_code. */
int32_t remedy_core_conpty_poll(
    uint64_t handle, uint8_t *out_exited, uint32_t *out_exit_code
);

/* TerminateProcess(1). Does not free the session — call close. */
int32_t remedy_core_conpty_kill(uint64_t handle);

/* Close one pipe end. which: 0 = stdin write, 1 = stdout read. Idempotent. */
int32_t remedy_core_conpty_close_pipe(uint64_t handle, uint32_t which);

/* Close remaining pipes, ClosePseudoConsole, CloseHandle(process), free
 * the session. The handle must not be reused. */
int32_t remedy_core_conpty_close(uint64_t handle);


/* ---- ABI 5 additive: policy + capability tokens --------------------------- */

/* HMAC-SHA-256 signing key for capability tokens. len must be >= 32; only the
 * first 32 bytes are used. Replaces any previous key and clears replay state.
 * No key is compiled into the library — callers supply test or secret-store
 * material at runtime. */
int32_t remedy_core_security_set_signing_key(const uint8_t *key, size_t len);

/* Forget the signing key and verifier replay set. */
int32_t remedy_core_security_clear_signing_key(void);

/* SHA-256 over a JSON argv string array (same framing as spawn). Writes 32
 * bytes into out_hash when out_hash_len >= 32. Bound to argv alone: a token
 * issued over this hash says nothing about the environment the spawn will
 * receive — prefer remedy_core_policy_hash_spawn. */
int32_t remedy_core_policy_hash_argv(
    const uint8_t *argv_json, size_t argv_len,
    uint8_t *out_hash, size_t out_hash_len
);

/* SHA-256 over argv plus the caller-supplied environment overrides and the
 * replacement flag — the operation hash an authorized spawn recomputes from
 * the env_json it actually receives, so a token cannot be minted for one
 * environment and spent on another. env_json takes the same shapes as the
 * spawn exports (an object of string values, or {"env": {...},
 * "replace_env": bool}); replace_env requests replacement for the plain
 * object shape. Empty env_json with replace_env 0 is byte-identical to
 * remedy_core_policy_hash_argv. */
int32_t remedy_core_policy_hash_spawn(
    const uint8_t *argv_json, size_t argv_len,
    const uint8_t *env_json, size_t env_len,
    uint8_t replace_env,
    uint8_t *out_hash, size_t out_hash_len
);

/* Strict environment binding, process-wide. Enabled (1): an authorized spawn
 * carrying environment overrides requires a token whose operation hash covers
 * them; an argv-only hash is refused with ACCESS_DENIED. Disabled (0,
 * default): argv-only tokens are still accepted while callers migrate.
 * Env-less spawns verify identically either way. */
int32_t remedy_core_policy_env_strict(uint8_t enabled);

/* Issue a v2 capability token (169 bytes). operation_hash is 32 bytes; nonce
 * is 16 bytes; out_token_len must be >= 169. Requires a signing key. */
int32_t remedy_core_capability_issue(
    const uint8_t *subject, size_t subject_len,
    const uint8_t *scope, size_t scope_len,
    const uint8_t *operation_hash, size_t operation_hash_len,
    uint64_t rights_bits,
    uint64_t issued_at_ms,
    uint64_t expires_at_ms,
    const uint8_t *nonce, size_t nonce_len,
    uint8_t *out_token, size_t out_token_len
);

/* Policy + token authorize, then hidden job-object spawn. argv[0] must be an
 * absolute path. Empty subject/scope default to agent:remedy / workspace:local.
 * owner_confirmed is 0/1. Token failures and policy denials → ACCESS_DENIED. */
int32_t remedy_core_process_spawn_authorized(
    const uint8_t *argv_json, size_t argv_len,
    const uint8_t *cwd, size_t cwd_len,
    const uint8_t *env_json, size_t env_len,
    const uint8_t *token, size_t token_len,
    const uint8_t *subject, size_t subject_len,
    const uint8_t *scope, size_t scope_len,
    uint8_t owner_confirmed,
    uint64_t now_ms,
    uint32_t *out_pid, uint64_t *out_handle
);

/* Policy + token authorize, then interactive 3-pipe spawn (win32 + linux).
 * Parent owns *out_stdin_write / *out_stdout_read / *out_stderr_read as OS
 * handles (Windows HANDLE or POSIX fd cast to uint64). *out_handle is the
 * same job/process-group handle as spawn_authorized — process_close does not
 * close the pipe ends. No soft unsigned fallback. */
int32_t remedy_core_process_spawn_piped_authorized(
    const uint8_t *argv_json, size_t argv_len,
    const uint8_t *cwd, size_t cwd_len,
    const uint8_t *env_json, size_t env_len,
    const uint8_t *token, size_t token_len,
    const uint8_t *subject, size_t subject_len,
    const uint8_t *scope, size_t scope_len,
    uint8_t owner_confirmed,
    uint64_t now_ms,
    uint32_t *out_pid, uint64_t *out_handle,
    uint64_t *out_stdin_write, uint64_t *out_stdout_read, uint64_t *out_stderr_read
);

/* Policy + token authorize, then ConPTY spawn (same auth contract). */
int32_t remedy_core_conpty_spawn_authorized(
    const uint8_t *argv_json, size_t argv_len,
    const uint8_t *cwd, size_t cwd_len,
    const uint8_t *env_json, size_t env_len,
    uint16_t cols, uint16_t rows,
    const uint8_t *token, size_t token_len,
    const uint8_t *subject, size_t subject_len,
    const uint8_t *scope, size_t scope_len,
    uint8_t owner_confirmed,
    uint64_t now_ms,
    uint32_t *out_pid, uint64_t *out_handle
);

/* Policy + token authorize, then one-shot hidden spawn with stdout/stderr
 * capture. Windows uses a kill-on-close job and kill-tree on timeout; other
 * platforms use the portable soft-capture primitive. timeout_ms 0 → 60000.
 * On timeout: *out_timed_out=1, *out_exit_code=1. Caller frees stdout/stderr
 * with remedy_core_free (NULL/0 when empty). Suitable for signal-cli receive
 * --json / send -m. */
int32_t remedy_core_process_exec_capture_authorized(
    const uint8_t *argv_json, size_t argv_len,
    const uint8_t *cwd, size_t cwd_len,
    const uint8_t *env_json, size_t env_len,
    const uint8_t *token, size_t token_len,
    const uint8_t *subject, size_t subject_len,
    const uint8_t *scope, size_t scope_len,
    uint8_t owner_confirmed,
    uint64_t now_ms,
    uint32_t timeout_ms,
    uint32_t *out_exit_code,
    uint8_t *out_timed_out,
    uint8_t **out_stdout, size_t *out_stdout_len,
    uint8_t **out_stderr, size_t *out_stderr_len
);

/* ---- ABI 5 additive: write jail / workdir roots --------------------------- */

/* Install write roots from a JSON string array of absolute paths. Empty array,
 * empty input, or null clears the jail (Full / unbound — no workdir gate).
 * Authorized spawn checks cwd under these roots and refuses auth-secret paths
 * always. Mutation-class absolute destinations outside roots are denied. */
int32_t remedy_core_write_jail_set_roots(
    const uint8_t *roots_json, size_t roots_len
);

/* Clear write roots (same as set_roots with []). */
int32_t remedy_core_write_jail_clear(void);

/* Check one path (optional cwd for relatives) against installed roots + auth.
 * OK / ACCESS_DENIED / INVALID_ARGUMENT. */
int32_t remedy_core_write_jail_check_path(
    const uint8_t *path, size_t path_len,
    const uint8_t *cwd, size_t cwd_len
);

/* Check argv_json + cwd with the same gate authorized spawn uses. */
int32_t remedy_core_write_jail_check_spawn(
    const uint8_t *argv_json, size_t argv_len,
    const uint8_t *cwd, size_t cwd_len
);

/* ---- ABI 5 additive: HostSession orchestration --------------------------- */

/* Argv JSON array Zig will authorize for HostSession open of *host*
 * (same PATH / SystemRoot resolution as open_authorized). Caller frees. */
int32_t remedy_core_host_session_argv(
    const uint8_t *host, size_t host_len,
    uint8_t **out_json, size_t *out_len
);

/* Open a persistent shell session. json_in:
 * {"host":"cmd"|"pwsh"|"posix", "cwd"?: "...", "env"?: {...}, "use_conpty"?: bool}.
 * Windows only (UNSUPPORTED elsewhere). Unsigned — tests / low-level use. */
int32_t remedy_core_host_session_open(
    const uint8_t *json_in, size_t json_in_len,
    uint64_t *out_handle
);

/* Authorize (policy + HMAC token + write-jail) then open. Same auth contract
 * as process/conpty spawn_authorized; token is consumed. */
int32_t remedy_core_host_session_open_authorized(
    const uint8_t *json_in, size_t json_in_len,
    const uint8_t *token, size_t token_len,
    const uint8_t *subject, size_t subject_len,
    const uint8_t *scope, size_t scope_len,
    uint8_t owner_confirmed,
    uint64_t now_ms,
    uint64_t *out_handle
);

/* Run one command in the session. On success *out_json is
 * {exit_code,stdout,stderr,cwd,timed_out,interactive,host,used_conpty}. */
int32_t remedy_core_host_session_run(
    uint64_t handle,
    const uint8_t *command, size_t command_len,
    uint32_t timeout_ms,
    uint8_t **out_json, size_t *out_len
);

/* Probe the session cwd (sentinel-wrapped). Empty string when unknown. */
int32_t remedy_core_host_session_cwd(
    uint64_t handle,
    uint8_t **out_utf8, size_t *out_len
);

/* Close pipes / ConPTY / kill tree and free the session. */
int32_t remedy_core_host_session_close(uint64_t handle);

/* Pure protocol: {"host","command","sentinel"} → {"wrapped"}. Portable. */
int32_t remedy_core_host_session_wrap(
    const uint8_t *json_in, size_t json_in_len,
    uint8_t **out_json, size_t *out_len
);

/* Pure protocol: {"text","sentinel","command"?,"conpty"?} → {"exit_code","body"}. */
int32_t remedy_core_host_session_split(
    const uint8_t *json_in, size_t json_in_len,
    uint8_t **out_json, size_t *out_len
);

/* ---- ABI 5 additive: host diagnose / dialect / stretch -------------------- */

/* Classify a failed host command. Input JSON:
 * {command, stdout?, stderr?, exit_code?, translated?, timed_out?, host?}.
 * Output JSON: {code, message, rewritten, hint, notes}. Caller frees. */
int32_t remedy_core_diagnose_host_failure(
    const uint8_t *json_in, size_t json_in_len,
    uint8_t **out_json, size_t *out_len
);

/* Probe PATH tools into dialect JSON. persist!=0 writes ~/.remedy/host/dialect.json
 * (or <home>/host/dialect.json). Caller frees. */
int32_t remedy_core_dialect_probe(
    const uint8_t *home, size_t home_len,
    uint8_t persist,
    uint8_t **out_json, size_t *out_len
);

/* Load dialect.json, healing empty/sidecar fields via probe. Caller frees. */
int32_t remedy_core_dialect_load(
    const uint8_t *home, size_t home_len,
    uint8_t **out_json, size_t *out_len
);

/* Record a successful host command; returns updated dialect JSON. Caller frees. */
int32_t remedy_core_dialect_record_success(
    const uint8_t *home, size_t home_len,
    const uint8_t *command, size_t command_len,
    const uint8_t *note, size_t note_len,
    uint8_t **out_json, size_t *out_len
);

/* One-line host inject. Optional dialect_json skips disk when non-empty. */
int32_t remedy_core_dialect_format_line(
    const uint8_t *home, size_t home_len,
    const uint8_t *dialect_json, size_t dialect_json_len,
    uint8_t **out_utf8, size_t *out_len
);

/* Probe + persist home census (~/.remedy/host/home.json). force!=0 refreshes. */
int32_t remedy_core_stretch_home(
    const uint8_t *home, size_t home_len,
    uint8_t force,
    uint8_t **out_json, size_t *out_len
);

/* Load home.json or the JSON null literal when missing. Caller frees. */
int32_t remedy_core_stretch_load(
    const uint8_t *home, size_t home_len,
    uint8_t **out_json, size_t *out_len
);

/* Returns JSON true/false whether stretch is missing or older than stale_days. */
int32_t remedy_core_stretch_needs(
    const uint8_t *home, size_t home_len,
    int32_t stale_days,
    uint8_t **out_json, size_t *out_len
);

/* Compact "This home" inject line. Optional census_json skips disk. */
int32_t remedy_core_stretch_format_line(
    const uint8_t *home, size_t home_len,
    const uint8_t *census_json, size_t census_json_len,
    uint8_t **out_utf8, size_t *out_len
);

/* Longer /whoami and /stretch block. Optional census_json skips disk. */
int32_t remedy_core_stretch_format_whoami(
    const uint8_t *home, size_t home_len,
    const uint8_t *census_json, size_t census_json_len,
    uint8_t **out_utf8, size_t *out_len
);

/* ---- ABI 5 additive: shell-chain expand + execute ------------------------- */

/* Expand cmd /c or sh -c "A && B" into hops JSON.
 * Input: {"argv":[...], "project_path"?: "..."}.
 * Output: {"hops": null} or {"hops":[{"kind":"cd|mkdir|run",...}]}.
 * OK with hops null = not a chain (not an error). Caller frees. */
int32_t remedy_core_shell_chain_expand(
    const uint8_t *json_in, size_t json_in_len,
    uint8_t **out_json, size_t *out_len
);

/* Execute a shell chain from argv or hops.
 * Input: argv OR hops + cwd?/env?/timeout_ms?/project_path?.
 * Output: {exit_code,stdout,stderr,duration_ms,cwd,timed_out,aborted,
 * not_a_chain,hops_run}. Jail/policy denies return OK with exit_code -1 and
 * stderr in the "Blocked by write jail" / "Blocked by security policy" family.
 * abort_flag may be null; non-zero byte aborts between/during hops.
 * Caller frees. */
int32_t remedy_core_shell_chain_execute(
    const uint8_t *json_in, size_t json_in_len,
    const uint8_t *abort_flag,
    uint8_t **out_json, size_t *out_len
);

/* ---- ABI 5 additive: Connect Tailscale management ------------------------- */

/* UTF-8 JSON status object {installed,running,logged_in,tailnet_ipv4,version,
 * error}. Spawns only the discovered Tailscale CLI with fixed argv. Caller
 * frees with remedy_core_free. Never raises through the ABI — error text is
 * inside the JSON when Tailscale is missing or not ready. */
int32_t remedy_core_tailscale_status(uint8_t **out_json, size_t *out_len);

/* UTF-8 JSON {status,message,login_url,msi_path,installer_url} from
 * `tailscale up` (soft timeout keeps a printed login URL). Caller frees. */
int32_t remedy_core_tailscale_login(uint8_t **out_json, size_t *out_len);

/* Launch msiexec /i <absolute .msi path> detached (no kill-on-close job).
 * Windows only; other platforms return UNSUPPORTED. */
int32_t remedy_core_tailscale_launch_msi(
    const uint8_t *msi_path, size_t msi_len,
    uint32_t *out_pid
);

#ifdef __cplusplus
}
#endif

#endif
