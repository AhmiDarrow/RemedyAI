// Package httpapi serves the production local HTTP API for remedy-runtime.
// Packaged Desktop and `remedy serve` bind this server on 127.0.0.1:7400 (or a
// desktop-chosen loopback port). Python no longer starts uvicorn on :7400 —
// it remains the RMDY/ML worker path. tauri:dev still prefers a live Python
// sidecar unless REMEDY_RUNTIME_SIDECAR=1; packaged builds fail closed when
// remedy-runtime is missing (no soft Python dual-serve).
package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
	"github.com/AhmiDarrow/RemedyAI/native/go/events"
	"github.com/AhmiDarrow/RemedyAI/native/go/gateway"
	"github.com/AhmiDarrow/RemedyAI/native/go/hive"
	"github.com/AhmiDarrow/RemedyAI/native/go/scheduler"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// Version matches pyproject.toml; scripts/sync_version.py stamps this const.
const Version = "0.62.1"

// Config controls the minimal local API server.
type Config struct {
	// Token is the expected Bearer / X-Remedy-Token value. Empty resolves
	// from REMEDY_API_KEY or $REMEDY_HOME/auth/local_api_token (plaintext).
	Token string
	// HomeDir overrides REMEDY_HOME when resolving the on-disk token.
	HomeDir string
	// Version overrides the reported product version (tests).
	Version string
	// DBPath is the SQLite memory.db path. Empty derives from HomeDir /
	// REMEDY_HOME / ~/.remedy/memory.db (or :memory: when no home).
	DBPath string
	// WebUIDir overrides FindWebUIDir (tests / REMEDY_WEBUI_DIR equivalent).
	WebUIDir string
	// TurnRunner powers POST /api/sessions/{id}/messages and .../messages/stream.
	// Nil → 503 (matches Python when runtime is unavailable).
	TurnRunner TurnRunner
	// VoiceWorker powers speak/transcribe/install (Python speech lane).
	// Nil → status/settings still work; speak/transcribe return 503 + fallback.
	VoiceWorker VoiceWorker
	// RmbController starts/stops llama-server (Python/Zig process supervisor).
	// Nil → status/catalog/settings/HF still work; start returns 503.
	RmbController RmbController
	// VisionWorker powers activate/install/start/stop (Python vision lane).
	// Nil → status/catalog still work; mutate routes return 503.
	VisionWorker VisionWorker
}

// Server is the production local HTTP API served by remedy-runtime.
type Server struct {
	started  time.Time
	version  string
	token    string
	homeDir  string
	webUIDir string
	mux      *http.ServeMux
	sessions *sessionStore
	events   *sessionEventHub
	claims   *streamClaims
	runner   TurnRunner
	voice    VoiceWorker
	rmb      RmbController
	vision   VisionWorker
	hf       *hfProgress

	connectGW     *connect.Gateway
	messengerGW   *gateway.Gateway
	apiListenPort int

	tunnelOnce    sync.Once
	tunnel        *messengerTunnelState
	tunnelStarter tunnelProcessStarter

	// focusedSessionID mirrors host_bridge focused desktop tab (Connect Stop).
	focusedMu        sync.Mutex
	focusedSessionID string

	approvals *approvalQueue
	lifeHub   *lifeTaskHub

	bus               *events.Bus
	eventLogPath      string
	ephemeralEventDir string
	sched             *scheduler.Scheduler
	schedPath         string
	schedCancel       context.CancelFunc
	hiveMgr           *hive.Manager
	hiveFS            *hiveStore

	bridgeOnce sync.Once
	hostBridge *HostBridge
	termOnce   sync.Once
	termReg    *terminalRegistry

	updatesMu    sync.Mutex
	updatesCache map[string]updatesCacheEntry

	appCmd *appControlBus

	// lastUserActivityUnixNano powers /api/self-improve idle_s (0 = none yet).
	lastUserActivityUnixNano int64
}

// New builds a server with ping/status/turn-active, auth bootstrap, settings,
// i18n chrome catalogs, partner memory search/facts/persona-wipe, sessions CRUD,
// session LLM bind, attachments upload/get, messages list/create/stream, abort,
// session-events SSE, durable events bus, scheduler jobs, hive
// roster/spawn/assign/retire, Connect management (including Tailscale
// status/install/login), Connect me/stop, providers/models catalog (including
// custom endpoints + probe), usage ledger, updates check, skills/library
// routes, workspace/files/media routes, partner/approvals/plans/life-tasks/goals,
// WebUI, computer-use host bridge, ConPTY terminal, voice, RMB, vision, and
// telephony routes.
func New(cfg Config) (*Server, error) {
	version := cfg.Version
	if version == "" {
		version = Version
	}
	homeDir := strings.TrimSpace(cfg.HomeDir)
	token := cfg.Token
	if token == "" {
		token = ResolveToken(homeDir)
	}
	// First-run: generate + persist only when a home is explicit (HomeDir /
	// REMEDY_HOME). Avoid writing into the real ~/.remedy from bare tests.
	if token == "" && AuthEnabled() {
		if homeDir != "" || strings.TrimSpace(os.Getenv("REMEDY_HOME")) != "" {
			token = secret.EnsureLocalAPIToken(ResolveHomeDir(homeDir), "")
		}
	}
	store, err := openSessionStore(resolveDBPath(cfg))
	if err != nil {
		return nil, fmt.Errorf("open session store: %w", err)
	}
	home := ResolveHomeDir(homeDir)
	s := &Server{
		started:   time.Now(),
		version:   version,
		token:     token,
		homeDir:   homeDir,
		webUIDir:  strings.TrimSpace(cfg.WebUIDir),
		mux:       http.NewServeMux(),
		sessions:  store,
		events:    newSessionEventHub(),
		claims:    newStreamClaims(),
		runner:    cfg.TurnRunner,
		voice:     cfg.VoiceWorker,
		rmb:       cfg.RmbController,
		vision:    cfg.VisionWorker,
		hf:        newHFProgress(),
		approvals: newApprovalQueue(),
		lifeHub:   newLifeTaskHub(),
		hiveMgr:   hive.New(context.Background(), 64),
		hiveFS:    openHiveStore(home),
		appCmd:    newAppControlBus(),
	}
	_ = s.approvals.SyncFromConfig(LoadConfig(homeDir))
	// Partner trust loop: Auto/Full must unlock coding mutations; Ask must
	// enqueue into the same queue the Desktop banner polls.
	if cr, ok := s.runner.(*CognitionTurnRunner); ok && cr != nil {
		cr.Approvals = s.approvals
		if cr.HomeDir == "" {
			cr.HomeDir = home
		}
		if cr.Registry != nil {
			cr.Policy = &RegistryPolicy{Registry: cr.Registry, Approvals: s.approvals}
		}
		// Browser-rail tools (computer.navigate) need the live HostBridge.
		_ = cr.AttachRailTools(s.bridge)
		// Settings Tool ABI (settings.get / settings.patch) uses the apply path.
		_ = cr.AttachSettingsTools(func() *Server { return s })
	}
	if err := s.openEventBus(home); err != nil {
		_ = store.Close()
		s.hiveMgr.Shutdown()
		return nil, fmt.Errorf("open event bus: %w", err)
	}
	s.initScheduler(home)
	s.mux.HandleFunc("GET /api/ping", s.handlePing)
	s.mux.HandleFunc("GET /api/status", s.handleStatus)
	s.mux.HandleFunc("GET /api/turn-active", s.handleTurnActive)
	s.mux.HandleFunc("GET /api/auth/local-bootstrap", s.handleLocalBootstrap)
	s.mux.HandleFunc("GET /api/auth/xai", s.handleXaiAuthStatus)
	s.mux.HandleFunc("GET /api/auth/xai/oauth-meta", s.handleXaiOAuthMeta)
	s.mux.HandleFunc("POST /api/auth/xai/login", s.handleXaiAuthLogin)
	s.mux.HandleFunc("GET /api/auth/xai/login/status", s.handleXaiAuthLoginStatus)
	s.mux.HandleFunc("POST /api/auth/xai/apikey", s.handleXaiAuthAPIKey)
	s.mux.HandleFunc("DELETE /api/auth/xai", s.handleXaiAuthLogout)
	s.mux.HandleFunc("GET /api/assistant/google", s.handleGoogleStatus)
	s.mux.HandleFunc("PUT /api/assistant/google/app", s.handleGoogleSaveApp)
	s.mux.HandleFunc("POST /api/assistant/google/oauth/start", s.handleGoogleOAuthStart)
	s.mux.HandleFunc("GET /api/assistant/google/oauth/status", s.handleGoogleOAuthStatus)
	s.mux.HandleFunc("GET /api/assistant/google/callback", s.handleGoogleOAuthCallback)
	s.mux.HandleFunc("DELETE /api/assistant/google", s.handleGoogleDisconnect)
	s.mux.HandleFunc("GET /api/settings", s.handleGetSettings)
	s.mux.HandleFunc("PUT /api/settings", s.handlePutSettings)
	s.mux.HandleFunc("GET /api/i18n", s.handleGetI18n)
	s.mux.HandleFunc("GET /api/memory/search", s.handleMemorySearch)
	s.mux.HandleFunc("GET /api/memory/facts", s.handleMemoryFacts)
	s.mux.HandleFunc("POST /api/memory/persona-wipe", s.handleMemoryPersonaWipe)
	s.mux.HandleFunc("GET /api/agents", s.handleListAgents)
	s.mux.HandleFunc("GET /api/commands", s.handleListCommands)
	s.mux.HandleFunc("GET /api/tools", s.handleListTools)
	s.mux.HandleFunc("POST /api/tools/invoke", s.handleInvokeTool)
	s.mux.HandleFunc("GET /api/app/command", s.handleAppCommand)
	s.mux.HandleFunc("GET /api/scratch", s.handleGetScratch)
	s.mux.HandleFunc("PUT /api/scratch", s.handlePutScratch)
	s.mux.HandleFunc("GET /api/diagnostics", s.handleDiagnostics)
	s.mux.HandleFunc("GET /api/self-inject/rounds", s.handleSelfInjectRounds)
	s.mux.HandleFunc("GET /api/self-improve", s.handleSelfImprove)
	s.mux.HandleFunc("GET /api/notifications", s.handleListNotifications)
	s.mux.HandleFunc("POST /api/notifications/read", s.handleMarkNotificationsRead)
	s.mux.HandleFunc("GET /api/metrics", s.handleMetrics)
	s.mux.HandleFunc("GET /api/coordination/presence", s.handleCoordinationPresence)
	s.mux.HandleFunc("GET /api/continuity/dashboard", s.handleContinuityDashboard)
	s.mux.HandleFunc("GET /api/nanoswarm/status", s.handleNanoswarmStatus)
	s.mux.HandleFunc("GET /api/nanoswarm/token/status", s.handleNanoswarmTokenStatus)
	s.mux.HandleFunc("POST /api/projects/scan", s.handleProjectsScan)
	s.mux.HandleFunc("GET /api/sessions", s.handleListSessions)
	s.mux.HandleFunc("POST /api/sessions", s.handleCreateSession)
	s.mux.HandleFunc("POST /api/sessions/bulk-project", s.handleBulkSetSessionProject)
	s.mux.HandleFunc("POST /api/sessions/import", s.handleImportSession)
	s.mux.HandleFunc("GET /api/sessions/{id}", s.handleGetSession)
	s.mux.HandleFunc("PATCH /api/sessions/{id}", s.handleUpdateSession)
	s.mux.HandleFunc("DELETE /api/sessions/{id}", s.handleDeleteSession)
	s.mux.HandleFunc("PUT /api/sessions/{id}/llm", s.handleSetSessionLLM)
	s.mux.HandleFunc("POST /api/sessions/{id}/llm", s.handleSetSessionLLM)
	s.mux.HandleFunc("POST /api/sessions/{id}/attachments", s.handleUploadAttachment)
	s.mux.HandleFunc("GET /api/sessions/{id}/attachments/{filename}", s.handleGetAttachment)
	s.mux.HandleFunc("GET /api/sessions/{id}/messages", s.handleListMessages)
	s.mux.HandleFunc("POST /api/sessions/{id}/messages", s.handleSendMessage)
	s.mux.HandleFunc("POST /api/sessions/{id}/messages/stream", s.handleStreamMessage)
	s.mux.HandleFunc("POST /api/sessions/{id}/messages/{msg_id}/edit", s.handleEditFromMessage)
	s.mux.HandleFunc("POST /api/sessions/{id}/abort", s.handleAbortSession)
	s.mux.HandleFunc("GET /api/sessions/{id}/export", s.handleExportSession)
	s.mux.HandleFunc("POST /api/sessions/{id}/steer", s.handleSteerSession)
	s.mux.HandleFunc("GET /api/sessions/{id}/todos", s.handleSessionTodos)
	s.mux.HandleFunc("GET /api/sessions/{id}/timeline", s.handleSessionTimeline)
	s.mux.HandleFunc("POST /api/sessions/{id}/time-travel", s.handleSessionTimeTravel)
	s.mux.HandleFunc("POST /api/sessions/{id}/command", s.handleSessionCommand)
	s.mux.HandleFunc("GET /api/events/sessions", s.handleSessionEvents)
	s.mux.HandleFunc("GET /api/events", s.handleEventsReplay)
	s.mux.HandleFunc("GET /api/events/stream", s.handleEventsStream)
	s.mux.HandleFunc("GET /api/scheduler/jobs", s.handleSchedulerList)
	s.mux.HandleFunc("POST /api/scheduler/jobs", s.handleSchedulerAdd)
	s.mux.HandleFunc("POST /api/scheduler/jobs/{id}/cancel", s.handleSchedulerCancel)
	s.mux.HandleFunc("POST /api/scheduler/goals/ready", s.handleSchedulerGoalReady)
	s.mux.HandleFunc("GET /api/hive/roster", s.handleHiveRoster)
	s.mux.HandleFunc("POST /api/hive/spawn", s.handleHiveSpawn)
	s.mux.HandleFunc("POST /api/hive/assign", s.handleHiveAssign)
	s.mux.HandleFunc("POST /api/hive/retire", s.handleHiveRetire)
	s.mux.HandleFunc("GET /api/connect", s.handleGetConnect)
	s.mux.HandleFunc("PUT /api/connect", s.handlePutConnect)
	s.mux.HandleFunc("GET /api/connect/addresses", s.handleConnectAddresses)
	s.mux.HandleFunc("POST /api/connect/pair/start", s.handleConnectPairStart)
	s.mux.HandleFunc("POST /api/connect/pause", s.handleConnectPause)
	s.mux.HandleFunc("POST /api/connect/resume", s.handleConnectResume)
	s.mux.HandleFunc("POST /api/connect/devices/{id}/revoke", s.handleConnectRevoke)
	s.mux.HandleFunc("GET /api/connect/tailscale/status", s.handleConnectTailscaleStatus)
	s.mux.HandleFunc("POST /api/connect/tailscale/install", s.handleConnectTailscaleInstall)
	s.mux.HandleFunc("POST /api/connect/tailscale/login", s.handleConnectTailscaleLogin)

	s.mux.HandleFunc("GET /api/messengers/tunnel", s.handleMessengerTunnelStatus)
	s.mux.HandleFunc("POST /api/messengers/tunnel/start", s.handleMessengerTunnelStart)
	s.mux.HandleFunc("POST /api/messengers/tunnel/stop", s.handleMessengerTunnelStop)
	s.mux.HandleFunc("POST /api/messengers/signal/ensure", s.handleMessengerSignalEnsure)
	s.mux.HandleFunc("GET /connect/me", s.handleConnectMe)
	s.mux.HandleFunc("GET /api/connect/me", s.handleConnectMe)
	s.mux.HandleFunc("POST /api/stop", s.handleConnectStop)
	s.mux.HandleFunc("GET /api/providers", s.handleListProviders)
	s.mux.HandleFunc("GET /api/providers/connected", s.handleListConnectedProviders)
	s.mux.HandleFunc("GET /api/providers/free", s.handleListFreeProviders)
	s.mux.HandleFunc("GET /api/providers/ollama/detect", s.handleOllamaDetect)
	s.mux.HandleFunc("POST /api/providers/custom", s.handleSaveCustomProvider)
	s.mux.HandleFunc("DELETE /api/providers/custom/{id}", s.handleDeleteCustomProvider)
	s.mux.HandleFunc("POST /api/providers/probe", s.handleProbeProvider)
	s.mux.HandleFunc("GET /api/models", s.handleListModels)
	s.mux.HandleFunc("GET /api/usage/summary", s.handleUsageSummary)
	s.mux.HandleFunc("GET /api/usage/series", s.handleUsageSeries)
	s.mux.HandleFunc("GET /api/usage/export", s.handleUsageExport)
	s.mux.HandleFunc("GET /api/updates/check", s.handleUpdatesCheck)
	s.mux.HandleFunc("GET /api/skills", s.handleListSkills)
	s.mux.HandleFunc("GET /api/skills/packs", s.handleGetSkillPacks)
	s.mux.HandleFunc("PUT /api/skills/packs", s.handlePutSkillPacks)
	s.mux.HandleFunc("GET /api/skills/learning/summary", s.handleSkillsLearningSummary)
	s.mux.HandleFunc("GET /api/skills/metrics/reuse", s.handleSkillsReuseMetrics)
	s.mux.HandleFunc("POST /api/skills/archive-unused", s.handleArchiveUnusedSkills)
	s.mux.HandleFunc("POST /api/skills/export", s.handleExportSkillsPack)
	s.mux.HandleFunc("POST /api/skills/import", s.handleImportSkillsPack)
	s.mux.HandleFunc("GET /api/skills/library/catalog", s.handleLibraryCatalog)
	s.mux.HandleFunc("GET /api/skills/library/search", s.handleLibrarySearch)
	s.mux.HandleFunc("GET /api/skills/library/suggest", s.handleLibrarySuggest)
	s.mux.HandleFunc("POST /api/skills/library/suggest/dismiss", s.handleLibrarySuggestDismiss)
	s.mux.HandleFunc("POST /api/skills/library/install", s.handleLibraryInstall)
	s.mux.HandleFunc("GET /api/skills/library/updates", s.handleLibraryUpdates)
	s.mux.HandleFunc("GET /api/skills/{name}", s.handleGetSkill)
	s.mux.HandleFunc("DELETE /api/skills/{name}", s.handleDeleteSkill)
	s.mux.HandleFunc("POST /api/skills/{name}/status", s.handleSetSkillStatus)
	s.mux.HandleFunc("POST /api/skills/{name}/quarantine", s.handleSetSkillQuarantine)
	s.mux.HandleFunc("PUT /api/skills/{name}/body", s.handlePutSkillBody)
	s.mux.HandleFunc("POST /api/skills/{name}/feedback", s.handleSkillFeedback)
	s.mux.HandleFunc("GET /api/workspace", s.handleGetWorkspace)
	s.mux.HandleFunc("GET /api/files", s.handleListFiles)
	s.mux.HandleFunc("GET /api/files/search", s.handleSearchFiles)
	s.mux.HandleFunc("GET /api/media", s.handleServeMedia)
	s.mux.HandleFunc("GET /api/partner/status", s.handlePartnerStatus)
	s.mux.HandleFunc("GET /api/approvals", s.handleListApprovals)
	s.mux.HandleFunc("POST /api/approvals/{approval_id}/resolve", s.handleResolveApproval)
	s.mux.HandleFunc("GET /api/life-tasks/current", s.handleCurrentLifeTask)
	s.mux.HandleFunc("POST /api/life-tasks/act", s.handleActLifeTask)
	s.mux.HandleFunc("POST /api/life-tasks/probe", s.handleProbeLifeTask)
	s.mux.HandleFunc("GET /api/life-tasks", s.handleListLifeTasks)
	s.mux.HandleFunc("GET /api/life-tasks/{task_id}", s.handleGetLifeTask)
	s.mux.HandleFunc("GET /api/plans/latest", s.handleLatestPlan)
	s.mux.HandleFunc("GET /api/plans", s.handleListPlans)
	s.mux.HandleFunc("POST /api/plans", s.handleCreatePlan)
	s.mux.HandleFunc("GET /api/plans/{plan_id}", s.handleGetPlan)
	s.mux.HandleFunc("POST /api/plans/{plan_id}/status", s.handleSetPlanStatus)
	s.mux.HandleFunc("POST /api/plans/{plan_id}/steps/status", s.handleSetPlanStepStatus)
	s.mux.HandleFunc("GET /api/checkpoints/latest", s.handleLatestCheckpoint)
	s.mux.HandleFunc("GET /api/checkpoints", s.handleListCheckpoints)
	s.mux.HandleFunc("GET /api/goals", s.handleListGoals)
	s.mux.HandleFunc("POST /api/goals", s.handleCreateGoal)
	s.mux.HandleFunc("POST /api/goals/activity/clear", s.handleClearGoalActivity)
	s.mux.HandleFunc("PATCH /api/goals/{goal_id}", s.handlePatchGoal)
	s.mux.HandleFunc("DELETE /api/goals/{goal_id}", s.handleDeleteGoal)
	s.mux.HandleFunc("POST /api/computer/host/hello", s.handleComputerHostHello)
	s.mux.HandleFunc("GET /api/computer/host/status", s.handleComputerHostStatus)
	s.mux.HandleFunc("GET /api/computer/ui/command", s.handleComputerUICommand)
	s.mux.HandleFunc("POST /api/computer/ui/command/ack", s.handleComputerUICommandAck)
	s.mux.HandleFunc("POST /api/computer/capture", s.handleComputerCapture)
	s.mux.HandleFunc("GET /api/computer/jobs/next", s.handleComputerJobsNext)
	s.mux.HandleFunc("POST /api/computer/jobs/{job_id}/complete", s.handleComputerJobComplete)
	s.mux.HandleFunc("POST /api/computer/jobs/{job_id}/cancel", s.handleComputerJobCancel)
	s.mux.HandleFunc("OPTIONS /api/computer/a11y/push", s.handleComputerA11yPushOptions)
	s.mux.HandleFunc("POST /api/computer/a11y/push", s.handleComputerA11yPush)
	s.mux.HandleFunc("POST /api/terminal", s.handleTerminalOpen)
	s.mux.HandleFunc("GET /api/terminal/{terminal_id}/stream", s.handleTerminalStream)
	s.mux.HandleFunc("POST /api/terminal/{terminal_id}/input", s.handleTerminalInput)
	s.mux.HandleFunc("POST /api/terminal/{terminal_id}/resize", s.handleTerminalResize)
	s.mux.HandleFunc("DELETE /api/terminal/{terminal_id}", s.handleTerminalClose)
	s.mux.HandleFunc("GET /api/voice/status", s.handleVoiceStatus)
	s.mux.HandleFunc("POST /api/voice/settings", s.handleVoiceSettings)
	s.mux.HandleFunc("POST /api/voice/install", s.handleVoiceInstall)
	s.mux.HandleFunc("POST /api/voice/client-log", s.handleVoiceClientLog)
	s.mux.HandleFunc("POST /api/voice/speak", s.handleVoiceSpeak)
	s.mux.HandleFunc("POST /api/voice/transcribe", s.handleVoiceTranscribe)
	s.mux.HandleFunc("GET /api/rmb/status", s.handleRmbStatus)
	s.mux.HandleFunc("GET /api/rmb/catalog", s.handleRmbCatalog)
	s.mux.HandleFunc("POST /api/rmb/start", s.handleRmbStart)
	s.mux.HandleFunc("POST /api/rmb/stop", s.handleRmbStop)
	s.mux.HandleFunc("POST /api/rmb/settings", s.handleRmbSettings)
	s.mux.HandleFunc("POST /api/rmb/use", s.handleRmbUse)
	s.mux.HandleFunc("POST /api/rmb/hf/search", s.handleRmbHfSearch)
	s.mux.HandleFunc("POST /api/rmb/hf/files", s.handleRmbHfFiles)
	s.mux.HandleFunc("POST /api/rmb/hf/pull", s.handleRmbHfPull)
	s.mux.HandleFunc("GET /api/rmb/hf/progress", s.handleRmbHfProgress)
	s.mux.HandleFunc("POST /api/rmb/hf/cancel", s.handleRmbHfCancel)
	s.mux.HandleFunc("GET /api/webhooks/whatsapp", s.handleWhatsAppVerify)
	s.mux.HandleFunc("POST /api/webhooks/whatsapp", s.handleWhatsAppEvents)
	s.mux.HandleFunc("POST /api/webhooks/teams", s.handleTeamsActivity)
	s.mux.HandleFunc("POST /api/webhooks/google_chat", s.handleGoogleChatEvent)
	s.mux.HandleFunc("POST /api/webhook/{source}", s.handleGenericWebhook)
	s.mux.HandleFunc("GET /api/vision/status", s.handleVisionStatus)
	s.mux.HandleFunc("GET /api/vision/catalog", s.handleVisionCatalog)
	s.mux.HandleFunc("POST /api/vision/activate", s.handleVisionActivate)
	s.mux.HandleFunc("POST /api/vision/install", s.handleVisionInstall)
	s.mux.HandleFunc("POST /api/vision/install/cancel", s.handleVisionInstallCancel)
	s.mux.HandleFunc("POST /api/vision/reinstall-runtime", s.handleVisionReinstallRuntime)
	s.mux.HandleFunc("POST /api/vision/uninstall", s.handleVisionUninstall)
	s.mux.HandleFunc("POST /api/vision/start", s.handleVisionStart)
	s.mux.HandleFunc("POST /api/vision/stop", s.handleVisionStop)
	s.mux.HandleFunc("GET /api/telephony/status", s.handleTelephonyStatus)
	s.mux.HandleFunc("POST /api/telephony/terms", s.handleTelephonyTerms)
	s.mux.HandleFunc("POST /api/telephony/choose", s.handleTelephonyChoose)
	s.mountWebUI()
	return s, nil
}

// Close stops messenger, Connect, scheduler, hive, event bus, aborts turns, then releases sessions.
func (s *Server) Close() error {
	if s == nil {
		return nil
	}
	s.stopSchedulerLoop()
	s.stopMessengerGateway()
	s.stopConnectGateway()
	if s.hiveMgr != nil {
		s.hiveMgr.Shutdown()
		s.hiveMgr = nil
	}
	s.closeEventBus()
	if s.claims != nil {
		s.claims.AbortAll()
		s.claims.WaitTurns()
	}
	if s.sessions == nil {
		return nil
	}
	return s.sessions.Close()
}

// Handler returns the CORS + auth wrapped mux.
func (s *Server) Handler() http.Handler {
	return withCORS(s.withAuth(s.mux))
}

// Serve serves until the listener closes or ctx is canceled.
func (s *Server) Serve(ctx context.Context, ln net.Listener) error {
	s.started = time.Now()
	s.apiListenPort = listenPort(ln)
	s.startMessengerGateway()
	s.startConnectGateway(s.apiListenPort)
	s.startSchedulerLoop(ctx)
	httpServer := &http.Server{Handler: s.Handler()}
	errCh := make(chan error, 1)
	go func() {
		errCh <- httpServer.Serve(ln)
	}()
	var serveErr error
	select {
	case <-ctx.Done():
		// Cancel turns before HTTP Shutdown so SSE handlers can drain.
		if s.claims != nil {
			s.claims.AbortAll()
		}
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(shutdownCtx)
		err := <-errCh
		if err == nil || errors.Is(err, http.ErrServerClosed) {
			serveErr = ctx.Err()
		} else {
			serveErr = err
		}
	case err := <-errCh:
		if errors.Is(err, http.ErrServerClosed) {
			serveErr = nil
		} else {
			serveErr = err
		}
	}
	_ = s.Close()
	return serveErr
}

// ListenAndServe binds addr (must be loopback) and serves until ctx cancels.
// onBound receives the actual host:port once listening, before Serve blocks.
func ListenAndServe(ctx context.Context, addr string, cfg Config, onBound func(string)) error {
	if err := ValidateListenAddr(addr); err != nil {
		return err
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	defer ln.Close()
	if onBound != nil {
		onBound(ln.Addr().String())
	}
	s, err := New(cfg)
	if err != nil {
		return err
	}
	err = s.Serve(ctx, ln)
	if errors.Is(err, context.Canceled) || errors.Is(err, net.ErrClosed) {
		return nil
	}
	return err
}

// ValidateListenAddr requires a loopback host:port (port may be 0).
func ValidateListenAddr(addr string) error {
	host, port, err := net.SplitHostPort(addr)
	if err != nil {
		return fmt.Errorf("listen address: %w", err)
	}
	if port == "" {
		return errors.New("listen address must include a port")
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		return errors.New("listen address must be loopback (127.0.0.1 or ::1)")
	}
	return nil
}

func (s *Server) handlePing(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status":  "ok",
		"version": s.version,
		"ts":      float64(time.Now().UnixNano()) / 1e9,
		"native_runtime": map[string]any{
			"requested": "native",
			"effective": "native",
			"ready":     true,
			"components": map[string]any{
				"go":  map[string]any{"ready": true, "reason": "serving"},
				"zig": map[string]any{"ready": false, "reason": "not-probed"},
			},
		},
	})
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	uptime := formatUptime(time.Since(s.started))
	gwStats := map[string]any{
		"running":            false,
		"uptime":             uptime,
		"channels_active":    0,
		"events_processed":   0,
		"channels":           []string{},
		"started_at":         nil,
		"rate_limit_per_min": 0,
	}
	if s.messengerGW != nil {
		gwStats = s.messengerGW.StatsMap()
		// Prefer process uptime for the top-level status field; gateway
		// stats still expose their own started_at / uptime.
		if !s.messengerGW.Running() {
			gwStats["running"] = false
		}
	}
	body := map[string]any{
		"status":              "ok",
		"version":             s.version,
		"uptime":              uptime,
		"gateway":             gwStats,
		"memory_entries":      0,
		"skills_count":        0,
		"sessions_count":      0,
		"chat_sessions_count": 0,
		"event_bus":           s.bus != nil,
		"scheduler_jobs":      0,
		"hive_agents":         0,
	}
	if s.connectGW != nil {
		h := s.connectGW.Health()
		body["connect"] = map[string]any{
			"serving":   h.Serving,
			"listening": h.Listening,
			"bind_host": h.BindHost,
			"bind_port": h.BindPort,
			"healing":   h.Healing,
			"crashes":   h.Crashes,
		}
	}
	if s.sched != nil {
		body["scheduler_jobs"] = len(s.sched.Jobs())
	}
	if s.hiveMgr != nil {
		body["hive_agents"] = len(s.hiveMgr.List())
	}
	// Unauthenticated liveness must not open SQLite or report skill/session counts.
	authed := s.token == "" || requestAuthorized(r, s.token)
	if !authed {
		writeJSON(w, http.StatusOK, body)
		return
	}
	body["skills_count"] = s.skillsCount()
	if s.sessions != nil {
		if mem, summaries, chats, err := s.sessions.StatusCounts(); err == nil {
			body["memory_entries"] = mem
			body["sessions_count"] = summaries
			body["chat_sessions_count"] = chats
		}
	}
	writeJSON(w, http.StatusOK, body)
}

func (s *Server) handleTurnActive(w http.ResponseWriter, _ *http.Request) {
	active := s.claims != nil && s.claims.AnyActive()
	writeJSON(w, http.StatusOK, map[string]any{
		"status": "ok",
		"active": active,
	})
}

func formatUptime(d time.Duration) string {
	if d < 0 {
		d = 0
	}
	secs := int(d.Seconds())
	h := secs / 3600
	m := (secs % 3600) / 60
	s := secs % 60
	if h > 0 {
		return fmt.Sprintf("%dh%dm%ds", h, m, s)
	}
	if m > 0 {
		return fmt.Sprintf("%dm%ds", m, s)
	}
	return fmt.Sprintf("%ds", s)
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
