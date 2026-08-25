"""Pending tool-approval queue for high-impact actions (partner trust loop)."""

from __future__ import annotations

import contextlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

APPROVAL_MODES = ("ask", "auto", "full")


def normalize_approval_mode(raw: str | None) -> str:
    """ask | auto | full. Unknown values fall back to ask."""
    m = (raw or "ask").strip().lower()
    if m in ("full", "warn", "owner", "yolo", "unrestricted"):
        return "full"
    if m in ("auto", "auto-approve", "thumbs-up", "trust"):
        return "auto"
    if m in APPROVAL_MODES:
        return m
    return "ask"


def is_full_approval(raw: str | None = None) -> bool:
    """True when the owner chose Full (warn) — write jail must not block."""
    if raw:
        return normalize_approval_mode(raw) == "full"
    try:
        return APPROVALS.mode == "full"
    except Exception:
        return False


# Owner checkpoints (docs/LIFE_TASK_PARTNER.md §2.2): money / payment /
# irreversible-purchase actions on the computer ALWAYS stop for the owner —
# no approval mode (auto/full), no turn flag, no optimization may skip them.
# Scope: computer_* tools only. Coding/build tools (bash_exec, file_write, …)
# are deliberately untouched so frontier-model coding agency keeps full flow.
SENSITIVE_PREFIX = "Owner checkpoint"


def checkpoint_blocks_recovery(reason: str | None) -> bool:
    """Payment / credentials / send checkpoints cannot be retried as 'recovery'."""
    return str(reason or "").startswith(SENSITIVE_PREFIX)

_SENSITIVE_COMPUTER_RE = re.compile(
    r"(?is)\b("
    # Purchase finalization
    r"place\s+(?:your\s+)?order|pay\s+now|buy\s+now|order\s+now|purchase\s+now|"
    r"confirm\s+(?:&\s*)?(?:order|purchase|payment|pay|subscription)|"
    r"complete\s+(?:order|purchase|payment|checkout)|"
    r"submit\s+(?:order|payment)|"
    r"start\s+(?:your\s+)?subscription|"
    # Money movement
    r"send\s+money|transfer\s+funds|wire\s+transfer|donate\s+now|"
    # Payment credentials as click/typed context
    r"card\s*number|cvv|cvc|security\s+code"
    r")\b"
    # Vault secret use (handle-based fill) — always an owner moment
    r"|vault=|\{\{\s*vault:"
)


# Mutation computer tools that can finalize a purchase on a payment surface.
_MUTATION_COMPUTER_TOOLS = frozenset(
    {
        "computer_click",
        "computer_act",
        "computer_key",
        "computer_type",
        "computer_drag",
        "computer_press_hold",
        "computer_select",
        "computer_fill",
    }
)
# Page-context signals that we're on a checkout / payment surface. When the
# current rail page looks like this AND a mutation's target could not be
# classified (coordinate click, bare Enter, unresolved ref), we fail closed —
# without prompting on every keypress during ordinary browsing.
_PAYMENT_SURFACE_RE = re.compile(
    r"(?is)("
    r"/(checkout|cart|payment|billing|pay|order|purchase|donate)\b"
    r"|checkout\.|\bcheckout\b|\bpayment\b|\bbilling\b"
    r"|place\s+order|pay\s+now|order\s+summary|order\s+total|"
    r"card\s*number|cardholder|cvv|cvc|expiry|billing\s+address"
    r")"
)
# Keys that can submit a focused payment form.
_SUBMIT_KEYS = frozenset({"enter", "return", "\n", "space", " "})
# Checkout buttons whose copy is too short for _SENSITIVE_COMPUTER_RE
# ("Submit", "Pay", "Confirm", "Continue") but still finalize a purchase
# when the live page is already a payment surface.
_SUBMIT_LABEL_RE = re.compile(
    r"(?is)\b("
    r"submit|pay(?:ment)?|confirm|continue|complete|checkout|"
    r"place|buy|order|donate"
    r")\b"
)


def looks_like_payment_surface(page_context: str) -> bool:
    return bool(_PAYMENT_SURFACE_RE.search(page_context or ""))


_CHALLENGE_WALL_RE = re.compile(
    r"(?is)("
    r"captcha|hcaptcha|recaptcha|turnstile|cloudflare|"
    r"i.?m not a robot|press.?and.?hold|press.?&.?hold|"
    r"human.?check|verify you are human"
    r")"
)


def challenge_wall_checkpoint(tool_name: str, page_context: str, label: str = "") -> str | None:
    """Owner handoff for CAPTCHA / press-and-hold walls (LIFE_TASK §7)."""
    if (tool_name or "").strip() != "computer_press_hold":
        return None
    blob = f"{page_context or ''} {label or ''}"
    if not _CHALLENGE_WALL_RE.search(blob):
        return None
    return (
        f"{SENSITIVE_PREFIX} — a human-check wall is on screen. Pause and let "
        "the owner complete it; do not press-and-hold or click the puzzle."
    )


def _origin_host(raw: str) -> str:
    """www-stripped hostname from a URL, host, or page-context blob."""
    s = (raw or "").strip()
    if not s:
        return ""
    token = s.split()[0]
    with contextlib.suppress(Exception):
        from urllib.parse import urlsplit

        blob = token if "://" in token else f"https://{token}"
        host = (urlsplit(blob).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host and "." in host:
            return host
    return ""


def payment_surface_checkpoint(
    tool_name: str,
    *,
    label_resolved: bool,
    page_context: str,
    key: str = "",
    label: str = "",
) -> str | None:
    """Owner checkpoint for a mutation on a payment surface.

    Fires when the live page looks like checkout/payment AND either:

    * the target is unclassifiable (coordinates / bare submit key /
      unresolved ref), or
    * the resolved label is submit-shaped (``Submit``, ``Pay``,
      ``Confirm``, ``Continue``) — those words miss
      :data:`_SENSITIVE_COMPUTER_RE` which wants ``place order`` /
      ``pay now``.

    Non-waivable, like the text classifier. Ordinary browsing clicks
    on a non-payment page are unaffected.
    """
    tool = (tool_name or "").strip()
    if tool not in _MUTATION_COMPUTER_TOOLS:
        return None
    if not looks_like_payment_surface(page_context):
        return None
    if tool == "computer_key" and (key or "").strip().lower() not in _SUBMIT_KEYS:
        return None  # non-submitting keystroke on a form field is fine
    if label_resolved:
        if _SUBMIT_LABEL_RE.search(label or ""):
            return (
                f"{SENSITIVE_PREFIX} — a purchase/payment page is open and this "
                f"control ({(label or 'submit')!r}) can complete the payment. "
                "Payment steps always need your go-ahead; confirm this is intended."
            )
        return None  # other readable labels still go through the text classifier
    return (
        f"{SENSITIVE_PREFIX} — a purchase/payment page is open and this action "
        "has no readable target (coordinate/key/ref). Payment steps always need "
        "your go-ahead; confirm this is intended."
    )


_CARD_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def _luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    checksum = 0
    for i, n in enumerate(reversed(d)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


def looks_like_raw_card(text: str) -> bool:
    """True when *text* contains a Luhn-valid 13–19 digit card number."""
    return any(_luhn_ok(m.group(0)) for m in _CARD_CANDIDATE_RE.finditer(text or ""))


def raw_secret_checkpoint(tool_name: str, typed_text: str) -> str | None:
    """Owner checkpoint when a raw card number is typed (not a vault handle).

    The vault path ({{vault:handle}}) is the intended way to fill payment
    details; typing a bare PAN would otherwise slip past the classifier
    (the summary hides the content). Fail closed on a Luhn-valid card so
    money details never get typed without the owner's explicit go-ahead.
    """
    tool = (tool_name or "").strip()
    if tool not in ("computer_type", "computer_act", "computer_fill"):
        return None
    if "{{vault:" in (typed_text or "") or "{{ vault:" in (typed_text or ""):
        return None  # vault handle — its own owner moment, bound to the site
    if looks_like_raw_card(typed_text):
        return (
            f"{SENSITIVE_PREFIX} — that looks like a raw card number. Payment "
            "details always need your go-ahead; prefer a saved vault entry so "
            "the value is bound to the site and never typed by guesswork."
        )
    return None


def sensitive_computer_checkpoint(tool_name: str, context: str) -> str | None:
    """Non-waivable owner-checkpoint reason for payment/purchase computer actions.

    Returns a reason string starting with :data:`SENSITIVE_PREFIX`, or None.
    Only ``computer_*`` tools are eligible — this never adds friction to
    coding/build tools.
    """
    tool = (tool_name or "").strip()
    if not tool.startswith("computer_"):
        return None
    c = context or ""
    m = _SENSITIVE_COMPUTER_RE.search(c)
    if not m:
        return None
    return (
        f"{SENSITIVE_PREFIX} — payment/purchase actions always need your "
        f"go-ahead (no approval mode skips this). Detected: {m.group(0)!r}"
    )


# Soft-ask (not hard-block): user can approve once / session / always-pattern
_ASK_PATTERNS = re.compile(
    r"(?is)"
    r"("
    r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|--force\b).{0,40}\S"
    r"|\bdel\s+/[sqf]"
    r"|\brmdir\s+/s"
    r"|\bRemove-Item\b.{0,80}(-Recurse|-Force)"
    r"|\bformat\s+[a-z]:"
    r"|\b(reg\s+delete|takeown\b|icacls\b.{0,40}/grant)"
    r"|\b(drop\s+database|truncate\s+table)\b"
    r"|\b(git\s+push\s+--force|git\s+reset\s+--hard)\b"
    r")"
)


@dataclass
class PendingApproval:
    id: str
    tool_name: str
    command: str
    reason: str
    session_id: str | None = None
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | approved | denied
    fingerprint: str = ""
    # Owner checkpoint (payment/purchase): mode flips never auto-approve it and
    # "always" approvals downgrade to session (one go-ahead ≠ standing consent).
    sensitive: bool = False
    # Live page host at create time. One-shot consume must match this host.
    origin: str = ""


class ApprovalQueue:
    """Process-local approval queue (desktop + CLI session)."""

    _MAX_APPROVED_FP = 1000
    _MAX_SESSIONS = 48

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, PendingApproval] = {}
        # Approved fingerprints for this process lifetime
        self._approved_fps: set[str] = set()
        # Session-scoped approvals
        self._session_fps: dict[str, set[str]] = {}
        self._session_order: list[str] = []
        # One-shot grants for sensitive (payment/purchase/vault) actions:
        # consumed on the very next matching call so an owner "yes" can never
        # be replayed on a later/different action (e.g. cross-site).
        # sid -> {fingerprint: origin-host} — origin-bound single-use grants
        self._one_shot: dict[str, dict[str, str]] = {}
        # ask | auto | full — status-bar cycle
        self._mode: str = "ask"

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def set_mode(self, mode: str) -> str:
        """Set approval mode: ``ask``, ``auto`` (in-project), or ``full`` (warn).

        Switching to **auto** or **full** clears ordinary pending prompts.
        """
        m = normalize_approval_mode(mode)
        with self._lock:
            prev = self._mode
            self._mode = m
            if m in ("auto", "full") and prev not in ("auto", "full"):
                # Auto-approve ordinary pending prompts. Owner-lock tools
                # (GitHub self-improve PR) stay pending — thumbs-up is not
                # permission to push to the public repo.
                for item in self._items.values():
                    if item.status != "pending":
                        continue
                    if item.tool_name in self.OWNER_LOCK_TOOLS:
                        continue
                    # Owner checkpoints (payment/purchase) stay pending — a mode
                    # flip is not a go-ahead for money.
                    if item.sensitive:
                        continue
                    item.status = "approved"
                    sid = item.session_id or "default"
                    self._add_session_fp(sid, item.fingerprint)
            return self._mode

    def sync_from_config(self, cfg: dict[str, Any] | None = None) -> str:
        """Align process mode with persisted config.toml when explicitly set.

        Fixes the restart bug: Settings UI read ``approval_mode=auto`` from TOML
        while the in-memory queue stayed on ``ask`` because AgentConfig omitted
        the field at boot.

        Only overrides mode when *cfg* contains ``approval_mode`` so unit tests
        that call ``set_mode("ask")`` with a partial monkeypatched config keep
        their intended mode.
        """
        if cfg is None:
            try:
                from remedy.interfaces.api_support import load_config

                cfg = load_config() or {}
            except Exception:
                cfg = {}
        if not isinstance(cfg, dict) or "approval_mode" not in cfg:
            with self._lock:
                return self._mode
        return self.set_mode(str(cfg.get("approval_mode") or "ask"))

    @staticmethod
    def fingerprint(tool_name: str, command: str) -> str:
        return f"{tool_name}::{(command or '').strip()}"

    def _add_session_fp(self, session_id: str, fp: str) -> None:
        """Add a fingerprint to a session, evicting oldest sessions when over capacity."""
        self._session_fps.setdefault(session_id, set()).add(fp)
        if session_id not in self._session_order:
            self._session_order.append(session_id)
        while len(self._session_fps) > self._MAX_SESSIONS:
            old_sid = self._session_order.pop(0)
            self._session_fps.pop(old_sid, None)

    def _trim_approved_fps(self) -> None:
        """Evict oldest approved fingerprints when over capacity."""
        while len(self._approved_fps) > self._MAX_APPROVED_FP:
            self._approved_fps.pop()

    # Tools that always require approval in ``ask`` mode (not only pattern match).
    # ``auto`` mode skips these on trusted scopes so "work until done" has full power.
    # Computer mutation tools are high-impact (OS click/type/launch) — same bar as shell.
    HIGH_IMPACT_TOOLS = frozenset(
        {
            "bash_exec",
            "run_python_file",
            "file_write",
            "file_edit",
            "skill_run",
            "mail_send",
            "analysis_run",
            "manuscript_build",
            # mail_reply and calendar_cancel_event both build a full
            # APPROVAL_REQUIRED item and both say in their own tool description
            # that they ask first — but neither name was ever listed here, so
            # needs_ask returned None and the whole block was dead in every
            # mode. Mail left the mailbox and appointments were deleted with no
            # prompt at all.
            "mail_reply",
            "calendar_cancel_event",
            # Rescheduling notifies every attendee; disconnecting deletes the
            # stored app password. Both acted silently in every mode.
            "calendar_update_event",
            "mail_disconnect",
            "computer_click",
            "computer_type",
            "computer_key",
            "computer_drag",
            "computer_act",
            "computer_app",
            "computer_select",
            "computer_fill",
            # Close only is gated in the tool; list/focus never call the gate.
            "computer_windows",
            # NOT computer_press_hold, deliberately: a press-and-hold is how a
            # CAPTCHA challenge is answered, and prompting on every one makes
            # them unusable. It is still in _MUTATION_COMPUTER_TOOLS, so the
            # payment-surface checkpoint stops it on a checkout page.
            # See test_press_hold_is_mutation_not_high_impact.
        }
    )
    # GitHub write for self-improve PRs — Auto / thumbs-up must never waive these.
    OWNER_LOCK_TOOLS = frozenset(
        {"self_improve_submit_issue", "self_improve_submit_pr"}
    )

    def needs_ask(self, command: str, *, tool_name: str = "") -> str | None:
        """Return reason string if action should require approval.

        **Power model (never stripped for the owner):**
        - ``ask`` (default, safe): high-impact tools + risk patterns prompt.
        - ``auto`` (in-project): no prompts on a trusted scope — build/write
          inside the focus folder. Write jail still blocks OS/home/siblings.
        - ``full`` (warn): no prompts; write jail does not block (auth stays
          closed). Tool output warns when a path would have been jailed.
        - ``untrusted`` access scope still always asks unless mode is ``full``.
        Hard security blocks (wipe/privilege) live in ``check_dangerous_command``
        and are separate from this partner-trust queue.

        **Owner checkpoints** (payment/purchase computer actions) are checked
        FIRST and are non-waivable: they ask in every mode including ``full``,
        and ignore turn-level skip flags. Computer tools only — coding tools
        keep the mode contract above.
        """
        sensitive = sensitive_computer_checkpoint(tool_name, command)
        if sensitive:
            return sensitive
        tool_early = (tool_name or "").strip()
        # Sending mail is irreversible and visible to a third party — same
        # class as purchase. No approval mode may waive it.
        if tool_early in ("mail_send", "mail_reply"):
            return (
                f"{SENSITIVE_PREFIX} — sending mail always needs your go-ahead "
                "(no approval mode skips this)."
            )
        # Re-sync mode when config explicitly sets approval_mode (desktop thumbs).
        # Scope always comes from config when available.
        cfg: dict[str, Any] = {}
        untrusted = False
        try:
            from remedy.interfaces.api_support import load_config

            loaded = load_config() or {}
            if isinstance(loaded, dict):
                cfg = loaded
            if "approval_mode" in cfg:
                self.sync_from_config(cfg)
            scope = str(cfg.get("access_scope") or "").lower()
            untrusted = scope in ("untrusted", "sandbox", "strict", "download")
        except Exception:
            untrusted = False
        try:
            from remedy.core.trust_profile import (
                normalize_trust_profile,
                profile_forces_high_impact_ask,
            )

            profile = normalize_trust_profile(cfg.get("trust_profile"))
            conservative = profile_forces_high_impact_ask(profile)
        except Exception:
            profile = None
            conservative = False
        # Local/RMB turn may skip Ask for this turn only (never persist Settings).
        if not untrusted:
            try:
                from remedy.core.turn_context import turn_skip_ask

                if turn_skip_ask():
                    return None
            except Exception:
                pass
        with self._lock:
            if self._mode == "full":
                return None
            # Conservative still asks for high-impact in Auto. Full stays Full.
            if self._mode == "auto" and not untrusted and not conservative:
                return None
        tool = (tool_name or "").strip()
        c = (command or "").strip()
        reason: str | None = None
        soft: str | None = None
        # Soft-risk signals (advisory) for clearer Ask banners — not hard blocks.
        try:
            from remedy.core.security import check_soft_dangerous_command

            soft = check_soft_dangerous_command(["bash", "-c", c] if c else [])
        except Exception:
            soft = None
        if untrusted and tool in self.HIGH_IMPACT_TOOLS:
            reason = "Untrusted workspace — approval required"
        if tool in self.HIGH_IMPACT_TOOLS and not reason:
            if tool == "bash_exec":
                reason = "Shell execution requires approval (bash_exec)"
            elif tool == "run_python_file":
                reason = "Running a Python file requires approval (run_python_file)"
            elif tool == "file_write":
                reason = "File write requires approval (file_write)"
            elif tool == "file_edit":
                reason = "File edit requires approval (file_edit)"
            elif tool == "skill_run":
                reason = "Skill script execution requires approval (skill_run)"
            elif tool == "analysis_run":
                reason = "Running an analysis script requires approval (analysis_run)"
            elif tool == "manuscript_build":
                reason = "Compiling a manuscript requires approval (manuscript_build)"
            elif tool == "mail_send":
                reason = "Sending email requires approval (mail_send)"
            elif tool == "mail_reply":
                reason = "Replying by email requires approval (mail_reply)"
            elif tool == "calendar_cancel_event":
                reason = "Cancelling an appointment requires approval "
                reason += "(calendar_cancel_event)"
            elif tool == "calendar_update_event":
                reason = "Rescheduling an appointment requires approval "
                reason += "(calendar_update_event)"
            elif tool == "mail_disconnect":
                reason = "Disconnecting the mailbox requires approval (mail_disconnect)"
            elif tool == "computer_windows":
                reason = "Closing a window requires approval (computer_windows)"
            elif tool.startswith("computer_"):
                reason = f"Computer control requires approval ({tool})"
        if not reason and c and _ASK_PATTERNS.search(c):
            reason = "High-impact / destructive command pattern"
        if reason and soft:
            reason = f"{reason} · soft-risk: {soft}"
        elif soft and not reason and tool == "bash_exec":
            # Soft-only: still ask in ask-mode for bash with risk signals
            reason = f"Soft-risk shell pattern — confirm to proceed ({soft})"
        # Guard nanobot: enrich reason with risk score (never hard-blocks here)
        try:
            from remedy.nanoswarm import get_swarm

            reason = get_swarm().guard.enrich_ask_reason(
                reason,
                tool_name=tool,
                command=c,
            )
        except Exception:
            pass
        if reason:
            try:
                from remedy.core.trust_profile import (
                    checkpoint_still_required,
                    profile_skips_high_impact_ask,
                )

                if (
                    profile is not None
                    and not untrusted
                    and profile_skips_high_impact_ask(profile)
                    and not reason.startswith(SENSITIVE_PREFIX)
                    and checkpoint_still_required(tool, c) is None
                ):
                    return None
            except Exception:
                pass
            if conservative:
                with self._lock:
                    auto_extra = self._mode == "auto"
                if auto_extra and not reason.startswith("Conservative trust"):
                    reason = f"Conservative trust — {reason}"
        return reason

    def is_approved(self, tool_name: str, command: str, session_id: str | None = None) -> bool:
        fp = self.fingerprint(tool_name, command)
        with self._lock:
            if fp in self._approved_fps:
                return True
            if session_id and fp in self._session_fps.get(session_id, set()):
                return True
            # Session only: same *git/gh verb family* counts for retries with
            # slightly different flags after Approve. Never expand process-wide
            # Always (_approved_fps) via family — that would let one Always on
            # `git status` cover `git commit` across sessions.
            if session_id and (tool_name or "").strip() == "bash_exec":
                fam = self._vcs_family(command)
                if fam:
                    for prev in self._session_fps.get(session_id, set()):
                        if not prev.startswith("bash_exec::"):
                            continue
                        if self._vcs_family(prev.split("::", 1)[-1]) == fam:
                            return True
        return False

    @staticmethod
    def _has_shell_chaining(command: str) -> bool:
        """True if *command* chains/redirects to another command. Quoted text
        (e.g. a commit message containing '&&') is ignored so it does not
        trip the check."""
        # Drop quoted spans so operators inside a message/arg don't count.
        unq = re.sub(r"'[^']*'", " ", command or "")
        unq = re.sub(r'"[^"]*"', " ", unq)
        return bool(re.search(r"(;|&&|\|\||\||`|\$\(|\n|\r|>|<|&\s|\bthen\b|\bdo\b)", unq))

    @classmethod
    def _vcs_family(cls, command: str) -> str | None:
        """Normalize git/gh push-family commands for sticky session approval.

        SECURITY: a family match only applies to a SINGLE clean git/gh
        statement. A chained/redirected command (git status && curl … | sh)
        must never inherit a prior `git status` approval — that was an approval
        gate bypass — so anything with shell chaining returns None and re-asks.
        """
        c = (command or "").strip().lower()
        if not c:
            return None
        if cls._has_shell_chaining(c):
            return None
        if re.search(r"\bgit\s+push\b", c):
            return "git_push"
        if re.search(
            r"\bgh\s+(pr\s+create|repo\s+create|auth\s+login|release\s+create)\b", c
        ):
            return "gh_publish"
        if re.search(r"\bgit\s+(status|diff|log|remote|branch)\b", c):
            return "git_read"
        if re.search(r"\bgit\s+(commit|add|tag)\b", c):
            return "git_write"
        return None

    def create(
        self,
        *,
        tool_name: str,
        command: str,
        reason: str,
        session_id: str | None = None,
        origin: str = "",
    ) -> PendingApproval:
        item = PendingApproval(
            id=uuid4().hex[:12],
            tool_name=tool_name,
            command=command,
            reason=reason,
            session_id=session_id,
            fingerprint=self.fingerprint(tool_name, command),
            sensitive=(reason or "").startswith(SENSITIVE_PREFIX),
            origin=_origin_host(origin),
        )
        with self._lock:
            self._items[item.id] = item
            # Prune old pending (>1h)
            cutoff = time.time() - 3600
            dead = [k for k, v in self._items.items() if v.created_at < cutoff]
            for k in dead:
                del self._items[k]
        return item

    def get(self, approval_id: str) -> PendingApproval | None:
        with self._lock:
            return self._items.get(approval_id)

    def list_pending(self, session_id: str | None = None) -> list[PendingApproval]:
        with self._lock:
            items = [v for v in self._items.values() if v.status == "pending"]
            if session_id:
                items = [v for v in items if v.session_id in (None, session_id)]
            return sorted(items, key=lambda x: x.created_at, reverse=True)

    def resolve(
        self,
        approval_id: str,
        *,
        approve: bool,
        scope: str = "session",
    ) -> PendingApproval | None:
        with self._lock:
            item = self._items.get(approval_id)
            if not item or item.status != "pending":
                return item
            item.status = "approved" if approve else "denied"
            if approve:
                sid = item.session_id or "default"
                if item.sensitive:
                    # Payment/purchase/vault: a single-use grant, NEVER a
                    # persisted fingerprint. Consumed by the next matching
                    # call so it cannot be replayed on a later or cross-site
                    # action (docs/LIFE_TASK_PARTNER.md §2.2).
                    self._one_shot.setdefault(sid, {})[item.fingerprint] = (
                        item.origin or ""
                    )
                elif scope == "always":
                    self._approved_fps.add(item.fingerprint)
                    self._trim_approved_fps()
                else:
                    self._add_session_fp(sid, item.fingerprint)
            return item

    def take_one_shot(
        self,
        tool_name: str,
        command: str,
        session_id: str | None = None,
        origin: str = "",
    ) -> bool:
        """Consume a one-shot sensitive grant if present. Single use.

        When the grant recorded an origin host, the live page must match.
        A mismatch drops the grant (no silent consume) so the owner is asked
        again — same action on a different site is a new decision.
        """
        fp = self.fingerprint(tool_name, command)
        sid = session_id or "default"
        live = _origin_host(origin)
        with self._lock:
            grants = self._one_shot.get(sid)
            if not grants or fp not in grants:
                return False
            granted = grants.get(fp)
            if granted is None:
                return False
            # Bound grant: live origin must be present AND match. An empty
            # live host (probe failed / tool omitted page_context) is a
            # deny, not a skip — consume-and-allow would send a card to
            # the wrong site.
            if granted:
                if not live or granted != live:
                    return False
            grants.pop(fp, None)
            if not grants:
                self._one_shot.pop(sid, None)
            return True

    @staticmethod
    def plain_summary(item: PendingApproval) -> str:
        """One plain-language sentence a non-technical owner can act on.

        Approval cards lead with this; the raw command stays available under a
        details expander (docs/LIFE_TASK_PARTNER.md §3).
        """
        tool = (item.tool_name or "").strip()
        cmd = (item.command or "").strip()

        def _field(name: str) -> str:
            m = re.search(rf"{name}='([^']*)'", cmd) or re.search(
                rf'{name}="([^"]*)"', cmd
            )
            return (m.group(1) if m else "").strip()

        if tool.startswith("computer_"):
            click = _field("click") or _field("text")
            url = _field("url")
            where = f" on {url}" if url else " on the current page"
            if item.sensitive:
                lead = "Remedy wants your go-ahead for a payment step"
                svm = re.search(r"vault=([a-z0-9._,-]+)", cmd)
                if svm:
                    return (
                        f"{lead}: fill your stored secret {svm.group(1)!r}"
                        f"{where} (the value stays encrypted — the AI never "
                        "sees it)."
                    )
                if click:
                    return f"{lead}: press {click!r}{where}."
                return f"{lead}{where}."
            if tool == "computer_app":
                app = _field("app")
                return f"Remedy wants to open {app or 'an app'} on this PC."
            vm = re.search(r"vault=([a-z0-9._,-]+)", cmd)
            if vm:
                lead = (
                    f"Remedy wants to fill your stored secret {vm.group(1)!r}"
                    f"{where} (the value stays encrypted — the AI never sees it)"
                )
                if click:
                    lead += f", after pressing {click!r}"
                return lead + "."
            if click:
                return f"Remedy wants to press {click!r}{where}."
            if tool == "computer_type":
                return "Remedy wants to type into the focused field."
            return f"Remedy wants to use the computer ({tool.removeprefix('computer_')})."
        if tool == "bash_exec":
            return f"Remedy wants to run a command: {cmd[:120]}"
        if tool in ("file_write", "file_edit"):
            return f"Remedy wants to change a file: {cmd[:120]}"
        if tool == "mail_send":
            return "Remedy wants to send an email."
        return f"Remedy wants to run {tool}: {cmd[:120]}"

    def to_public(self, item: PendingApproval) -> dict[str, Any]:
        soft_risk = None
        if "soft-risk:" in (item.reason or "").lower() or "Soft-risk" in (item.reason or ""):
            soft_risk = item.reason
        return {
            "id": item.id,
            "tool_name": item.tool_name,
            "command": item.command[:500],
            "reason": item.reason,
            "summary": self.plain_summary(item),
            "sensitive": item.sensitive,
            "soft_risk": soft_risk,
            "session_id": item.session_id,
            "status": item.status,
            "created_at": item.created_at,
            "approval_mode_hint": (
                "One-time go-ahead for payment steps — these ask in every mode. "
                if item.sensitive
                else "Approve for this session, or set Approvals → Auto (in-project) "
                "to finish work without prompts. Full (warn) turns the write jail "
                "into a warning only — auth secrets stay closed."
            ),
        }


# Singleton used by agent + API
APPROVALS = ApprovalQueue()
