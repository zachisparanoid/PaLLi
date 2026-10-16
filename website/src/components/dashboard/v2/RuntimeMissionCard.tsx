"use client";

import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Clock3,
  House,
  Link2,
  Loader2,
  Mic,
  PauseCircle,
  PlayCircle,
  RefreshCw,
  Shield,
  Sparkles,
  Smartphone,
  TerminalSquare,
} from "lucide-react";

import { apiDelete, apiGet, apiPatch, apiPost, getBackendWebSocketUrl } from "@/lib/api";
import { devLogError } from "@/lib/devLog";
import { useLocalMode } from "@/lib/useLocalMode";
import { useToast } from "@/components/ui/toast";
import { StatusPill } from "@/components/dashboard/v2/agentControlShared";

type RuntimeWarning = {
  code: string;
  severity: string;
  message: string;
};

type MissionControlActionLink = {
  label: string;
  href: string;
};

const RUNTIME_HEALTH_SECTION_HREF = "#runtime-health";
const VOICE_RUNTIME_SECTION_HREF = "#voice-runtime";
const OWNER_CONTROLS_HREF = "/account";

type ReplySurfaceAttentionInput = {
  family?: string;
  routing_kind?: string;
  route_changed?: boolean;
  missing_requirements?: string[];
  issue?: {
    message?: string;
  } | null;
  requested_provider_label?: string;
  requested_provider?: string;
  requested_model_reference?: string;
  effective_provider_label?: string;
  effective_provider?: string;
  effective_model_reference?: string;
  engine?: {
    requested?: string;
    requested_label?: string;
    effective?: string;
    effective_label?: string;
    fallback_active?: boolean;
    fallback_reason?: string;
    fallback_reason_label?: string;
  };
};

type BrowserVoicePathAttentionInput = {
  id?: string;
  missing_requirements?: string[];
  fallback_active?: boolean;
  fallback_reason?: string;
  fallback_reason_label?: string;
  requested_engine?: string;
  requested_engine_label?: string;
  effective_engine?: string;
  effective_engine_label?: string;
};

type VoiceSurfaceEngineAttentionInput = {
  platform?: string;
  label?: string;
  bridge_surface_enabled?: boolean;
  supported_engines?: Array<{ id: string; label: string }>;
  requested_engine?: string;
  requested_engine_label?: string;
  effective_engine?: string;
  effective_engine_label?: string;
  fallback_active?: boolean;
  fallback_reason?: string;
  fallback_reason_label?: string;
};

type LLMRecentActivityEntry = {
  recorded_at?: number;
  provider?: string;
  provider_label?: string;
  effective_provider?: string;
  effective_provider_label?: string;
  model?: string;
  effective_model?: string;
  requested_provider?: string;
  requested_provider_label?: string;
  requested_model?: string;
  effective_model_reference?: string;
  requested_model_reference?: string;
  route_changed?: boolean;
  log_context?: string;
  activity_kind?: string;
  activity_label?: string;
  lane?: string;
  lane_label?: string;
  is_response?: boolean;
  success?: boolean;
  error?: string;
};

type LLMResponseLaneSummary = {
  lane: string;
  lane_label?: string;
  call_count?: number;
  success_count?: number;
  failure_count?: number;
  last_response?: LLMRecentActivityEntry | null;
  last_successful_response?: LLMRecentActivityEntry | null;
  last_failed_response?: LLMRecentActivityEntry | null;
};

type BackendStartupStage = {
  stage_id?: string;
  label?: string;
  status?: string;
  required?: boolean;
  message?: string;
};

type BackendStartupSnapshot = {
  status?: string;
  started_at?: number;
  completed_at?: number;
  counts?: {
    ready?: number;
    skipped?: number;
    degraded?: number;
    failed?: number;
  };
  stages?: BackendStartupStage[];
};

type RuntimeMission = {
  owner_root_key: string;
  owner_user_id: number;
  generated_at: number;
  warnings: RuntimeWarning[];
  sections: {
    health: {
      status: string;
      host?: {
        host_platform?: string;
        host_backend?: string;
        display_server?: string;
        description?: string;
        capabilities?: Record<
          string,
          {
            status?: string;
            message?: string;
            updated_at?: number;
          }
        >;
      };
      backend?: {
        url?: string;
        status?: string;
        startup?: BackendStartupSnapshot | null;
        mission_control_summary?: string;
        mission_control_detail?: string;
      };
      frontend?: {
        url?: string;
        status?: string;
        mission_control_summary?: string;
        mission_control_detail?: string;
      };
      database?: {
        absolute_path?: string;
        writable?: boolean;
        mission_control_summary?: string;
        mission_control_detail?: string;
      };
      launcher?: {
        last_doctor?: {
          summary?: string;
          generated_at?: number;
        } | null;
      };
      doctor?: {
        status?: string;
        summary?: string;
        mission_control_summary?: string;
        mission_control_detail?: string;
        counts?: {
          core_blockers?: number;
          warnings?: number;
          ok?: number;
        };
      };
      plugins?: {
        status?: string;
        counts?: {
          total?: number;
          enabled?: number;
          errors?: number;
          warnings?: number;
        };
        restart_pending?: boolean;
      };
      channels?: {
        discord?: {
          enabled?: boolean;
          configured?: boolean;
        };
        telegram?: {
          enabled?: boolean;
          configured?: boolean;
        };
        slack?: {
          enabled?: boolean;
          configured?: boolean;
        };
        runtime?: {
          connected_platforms?: string[];
          platforms?: Array<Record<string, any>>;
        };
      };
      nodes?: {
        status?: string;
        counts?: {
          total?: number;
          connected?: number;
          artifacts?: number;
          invocations?: number;
          fallback_invocations?: number;
        };
        preferred_notification_node_id?: string;
        recent_recoveries?: Array<Record<string, any>>;
      };
      surface_reliability?: {
        counts?: Record<string, number>;
        recent_events?: Array<Record<string, any>>;
        summary?: string;
        attention_summary?: string;
        latest_event_summary?: string;
      };
      warnings?: Array<{
        code: string;
        severity: string;
        message: string;
      }>;
      runtime: {
        deployment: {
          mode: string;
          local_mode: boolean;
          dashboard_no_auth: boolean;
          owner_display_name: string;
        };
        operator: {
          id: number;
          email: string;
          discord_user_id?: string;
        };
        features: {
          discord_enabled?: boolean;
          telegram_enabled?: boolean;
          slack_enabled?: boolean;
          voice_enabled: boolean;
          agentic_enabled: boolean;
          agentic_mode: string;
          agentic_rollout_mode: string;
          privileged_autonomy_enabled: boolean;
        };
        llm: {
          default_provider: string;
          default_provider_label?: string;
          default_model_reference?: string;
          default_provider_configured?: boolean;
          mission_control_summary?: string;
          mission_control_detail?: string;
          mission_control_attention?: string;
          providers: Array<{ provider: string; label: string; configured: boolean }>;
          recent_activity?: {
            scope?: string;
            call_count?: number;
            success_count?: number;
            failure_count?: number;
            last_call?: LLMRecentActivityEntry | null;
            last_response?: LLMRecentActivityEntry | null;
            last_successful_call?: LLMRecentActivityEntry | null;
            last_successful_response?: LLMRecentActivityEntry | null;
            last_failed_call?: LLMRecentActivityEntry | null;
            last_failed_response?: LLMRecentActivityEntry | null;
            last_successful_chat_response?: LLMRecentActivityEntry | null;
            last_successful_voice_response?: LLMRecentActivityEntry | null;
            last_failed_chat_response?: LLMRecentActivityEntry | null;
            last_failed_voice_response?: LLMRecentActivityEntry | null;
            global_last_failed_response?: LLMRecentActivityEntry | null;
            response_lanes?: Array<LLMResponseLaneSummary>;
            recent_calls?: Array<LLMRecentActivityEntry>;
          };
          task_routing?: Record<
            string,
            {
              provider: string;
              provider_label: string;
              provider_known?: boolean;
              provider_configured?: boolean;
              model?: string;
              requested_provider?: string;
              requested_provider_label?: string;
              requested_provider_source?: string;
              requested_model?: string;
              requested_model_source?: string;
              requested_model_reference?: string;
              effective_provider?: string;
              effective_provider_label?: string;
              effective_provider_source?: string;
              effective_model?: string;
              effective_model_reference?: string;
              route_changed?: boolean;
              resolution_reason?: string;
              issue?: {
                code?: string;
                severity?: string;
                message?: string;
              } | null;
            }
          >;
          reply_surfaces?: Array<{
            id: string;
            label: string;
            family: string;
            routing_kind: string;
            task?: string;
            status: string;
            requested_provider?: string;
            requested_provider_label?: string;
            requested_provider_source?: string;
            requested_model?: string;
            requested_model_source?: string;
            requested_model_reference?: string;
            effective_provider?: string;
            effective_provider_label?: string;
            effective_provider_source?: string;
            effective_model?: string;
            effective_model_reference?: string;
            route_changed?: boolean;
            resolution_reason?: string;
            voice?: string;
            transcription_model?: string;
            missing_requirements?: string[];
            note?: string;
            engine?: {
              requested?: string;
              requested_label?: string;
              effective?: string;
              effective_label?: string;
              fallback_active?: boolean;
              fallback_reason?: string;
              fallback_reason_label?: string;
            };
            issue?: {
              code?: string;
              severity?: string;
              message?: string;
            } | null;
          }>;
          route_issues?: Array<{
            task?: string;
            provider?: string;
            severity?: string;
            message?: string;
          }>;
          routing_ready?: boolean;
          routing_issue_count?: number;
          critical_route_failure_count?: number;
        };
        voice: {
          bootstrap_ok: boolean;
          bootstrap_messages: string[];
          stt_provider: string;
          tts_provider: string;
          voice_engine_default?: string;
          voice_engine_default_label?: string;
          mission_control_summary?: string;
          mission_control_detail?: string;
          mission_control_attention?: string;
          saved_defaults: {
            voice_engine?: string;
            interaction_mode: string;
            presence_profile: string;
            focus_state: string;
            local_transport_mode: string;
            group_voice_behavior?: string;
          };
          surface_engines?: Array<{
            platform: string;
            surface: string;
            label: string;
            bridge_surface_enabled?: boolean;
            supported_engines?: Array<{ id: string; label: string }>;
            requested_engine?: string;
            requested_engine_label?: string;
            effective_engine?: string;
            effective_engine_label?: string;
            fallback_active?: boolean;
            fallback_reason?: string;
            fallback_reason_label?: string;
          }>;
          browser_paths?: Array<{
            id: string;
            label: string;
            status: string;
            available?: boolean;
            requested_engine?: string;
            requested_engine_label?: string;
            effective_engine?: string;
            effective_engine_label?: string;
            fallback_active?: boolean;
            fallback_reason?: string;
            fallback_reason_label?: string;
            model?: string;
            voice?: string;
            missing_requirements?: string[];
            note?: string;
          }>;
        };
      };
    };
    sessions: {
      status: string;
      summary?: string;
      attention_summary?: string;
      current_thread_summary?: string;
      counts: {
        total: number;
        active: number;
        threads?: number;
      };
      items: Array<Record<string, any>>;
      current_thread?: Record<string, any> | null;
      surface_sessions?: Array<Record<string, any>>;
      recent_workspaces?: Array<Record<string, any>>;
    };
    jobs: {
      status: string;
      summary?: string;
      attention_summary?: string;
      next_due_summary?: string;
      counts: {
        total: number;
        active: number;
        scheduled: number;
        failed: number;
      };
      items: Array<Record<string, any>>;
      next_due?: Record<string, any> | null;
    };
    runs: {
      status: string;
      summary?: string;
      attention_summary?: string;
      latest_activity_summary?: string;
      counts: {
        live_runs: number;
        deliveries: number;
        active_deliveries?: number;
        failed_deliveries?: number;
        pending_confirmations: number;
      };
      live_runs: Array<Record<string, any>>;
      recent_runs: Array<Record<string, any>>;
      deliveries: Array<Record<string, any>>;
      confirmations: Array<Record<string, any>>;
    };
    agent: {
      status: string;
      summary?: string;
      attention_summary?: string;
      current_focus_summary?: string;
      counts: {
        roots: number;
        live_runs: number;
        deliveries: number;
        failed_deliveries: number;
        pending_confirmations: number;
        active_deliveries: number;
      };
      roots: Array<Record<string, any>>;
      live_runs: Array<Record<string, any>>;
      deliveries: Array<Record<string, any>>;
      confirmations: Array<Record<string, any>>;
    };
    voice: {
      status: string;
      control_capabilities: {
        can_patch_local_runtime: boolean;
        has_live_sessions?: boolean;
      };
      counts?: {
        live_sessions?: number;
        browser_sessions?: number;
        browser_direct_sessions?: number;
        browser_direct_active_sessions?: number;
        discord_sessions?: number;
        fallback_sessions?: number;
      };
      saved_defaults: {
        voice_engine?: string;
        interaction_mode: string;
        presence_profile: string;
        focus_state: string;
        local_transport_mode: string;
        group_voice_behavior?: string;
      };
      browser_activity?: {
        status?: string;
        current_path?: string;
        current_path_label?: string;
        summary?: string;
        paths?: Array<{
          id: string;
          label: string;
          activity_state?: string;
          active?: boolean;
          active_sessions?: number;
          recent_sessions?: number;
          last_activity_at?: number;
          last_activity_detail?: string;
          last_floor_state?: string;
          last_floor_state_label?: string;
          last_activity_source?: string;
          last_activity_source_label?: string;
          last_mirrored_role?: string;
          last_mirrored_excerpt?: string;
          model?: string;
          voice?: string;
        }>;
        recent_sessions?: Array<{
          id: string;
          session_key?: string;
          path: string;
          path_label: string;
          activity_state?: string;
          started_at?: number;
          last_activity_at?: number;
          summary?: string;
          detail?: string;
          excerpt?: string;
          excerpt_label?: string;
          floor_state?: string;
          floor_state_label?: string;
          requested_engine?: string;
          requested_engine_label?: string;
          effective_engine?: string;
          effective_engine_label?: string;
          fallback_active?: boolean;
          fallback_reason?: string;
          fallback_reason_label?: string;
          last_activity_source?: string;
          last_activity_source_label?: string;
          model?: string;
          voice?: string;
          source?: string;
        }>;
      };
      sessions: Array<Record<string, any>>;
      active_local_session?: Record<string, any> | null;
      active_discord_sessions: Array<Record<string, any>>;
      persisted_sessions: Array<Record<string, any>>;
    };
    context: {
      status: string;
      state: Record<string, any>;
      source_health: Record<string, any>;
      recent_events: Array<Record<string, any>>;
      counts: {
        recent_events: number;
        degraded_sources: number;
      };
    };
    ambient: {
      status: string;
      counts: {
        recent_actions: number;
        completed: number;
        suppressed: number;
      };
      recent_actions: Array<Record<string, any>>;
      last_arrival_action?: Record<string, any> | null;
      cooldown?: {
        active: boolean;
        until?: number | null;
        remaining_seconds?: number;
      } | null;
    };
    operator: {
      status: string;
      state: {
        current_workspace_root?: string;
        current_workspace_label?: string;
        current_remote_name?: string;
        pinned_workspace_root?: string;
        pinned_workspace_label?: string;
        pinned_remote_name?: string;
        last_operator_action_id?: string;
        last_operator_activity_at?: number | null;
        recent_workspaces?: Array<Record<string, any>>;
      };
      host_platform?: {
        host_platform?: string;
        host_backend?: string;
        display_server?: string;
        description?: string;
      };
      host_capability_health?: Record<
        string,
        {
          status?: string;
          message?: string;
          updated_at?: number;
        }
      >;
      machine_context: {
        active_browser_session?: Record<string, any>;
        focused_window?: Record<string, any>;
      };
      desktop_perception?: {
        last_inspection?: Record<string, any>;
        last_resolved_target?: Record<string, any>;
      };
      surface_reliability?: {
        counts?: Record<string, number>;
        recent_events?: Array<Record<string, any>>;
        app_states?: Record<string, any>;
        node_states?: Record<string, any>;
      };
      focused_app_adapter?: Record<string, any>;
      last_inspection?: Record<string, any>;
      last_resolved_target?: Record<string, any>;
      app_adapters?: Array<{
        app_id: string;
        label: string;
        capabilities: string[];
        is_focused?: boolean;
        health?: {
          status?: string;
          message?: string;
        };
      }>;
      adapter_health: Record<
        string,
        {
          status?: string;
          message?: string;
          updated_at?: number;
        }
      >;
      counts: {
        recent_actions: number;
        active_actions: number;
        pending_confirmations: number;
        blocked_actions: number;
        recent_workflows?: number;
        active_workflows?: number;
        waiting_workflows?: number;
        paused_workflows?: number;
        blocked_workflows?: number;
        recent_memories?: number;
        open_followthrough?: number;
        review_queue?: number;
        recent_wins?: number;
      };
      active_actions: Array<Record<string, any>>;
      recent_actions: Array<Record<string, any>>;
      blocked_actions: Array<Record<string, any>>;
      pending_confirmations: Array<Record<string, any>>;
      active_workflows?: Array<Record<string, any>>;
      waiting_workflows?: Array<Record<string, any>>;
      paused_workflows?: Array<Record<string, any>>;
      blocked_workflows?: Array<Record<string, any>>;
      recent_workflows?: Array<Record<string, any>>;
      current_workflow?: Record<string, any> | null;
      current_step?: Record<string, any> | null;
      current_question?: Record<string, any> | null;
      current_wait?: {
        state?: string;
        reason_code?: string;
        resume_policy?: string;
        prompt?: string;
        payload_summary?: string;
      } | null;
      last_resume_brief?: string;
      auto_resume_eligible?: boolean;
      last_auto_resume_at?: number | null;
      closure_state?: string;
      latest_outcome?: Record<string, any> | null;
      latest_handoff_summary?: string;
      latest_what_changed?: string;
      latest_remaining_work?: string;
      latest_recommended_next_action?: string;
      latest_recommended_action_label?: string;
      last_handoff_at?: number | null;
      latest_memory?: Record<string, any> | null;
      recent_memories?: Array<Record<string, any>>;
      latest_followthrough?: Record<string, any> | null;
      open_followthrough?: Array<Record<string, any>>;
      next_followthrough_due?: Record<string, any> | null;
      followthrough_counts?: Record<string, number>;
      recent_workspaces?: Array<Record<string, any>>;
      review_queue?: Array<Record<string, any>>;
      recent_wins?: Array<Record<string, any>>;
      review_counts?: Record<string, number>;
      last_review_nudge?: Record<string, any> | null;
      next_review_due?: Record<string, any> | null;
      review_summary?: string;
      identity_sync_status?: string;
      progress_state?: string;
      progress_summary?: string;
      latest_meaningful_change?: string;
      next_step_preview?: string;
      strategy_family?: string;
      strategy_summary?: string;
      strategy_attempt_count?: number;
      strategy_last_shift_reason?: string;
      strategy_last_shift_at?: number | null;
      strategy_exhausted?: boolean;
      blocker_class?: string;
      blocker_summary?: string;
      recent_findings?: Array<Record<string, any>>;
      recent_outcomes?: Array<Record<string, any>>;
      drift_status?: string;
      same_step_no_progress_streak?: number;
      last_answer_summary?: string;
      last_trigger_source?: string;
    };
    reminders: {
      status: string;
      counts: {
        due: number;
        pending: number;
        total: number;
      };
      loop: {
        loop_state: string;
        last_check_started_at?: number | null;
        last_check_completed_at?: number | null;
        last_error?: string;
      };
      latest_dispatch?: {
        summary?: string;
        status_label?: string;
        activity_at?: number | null;
        delivered_in_label?: string;
      } | null;
      recent_loop_activity?: Array<Record<string, any>>;
      recent_deliveries?: Array<Record<string, any>>;
      due_reminders: Array<Record<string, any>>;
      pending_reminders: Array<Record<string, any>>;
    };
    heartbeat?: {
      status: string;
      controls: {
        can_check_now: boolean;
        can_pause: boolean;
        can_resume: boolean;
      };
      loop: {
        is_running: boolean;
        is_paused: boolean;
        loop_state: string;
        last_check_started_at?: number | null;
        last_check_completed_at?: number | null;
        last_error?: string;
        last_decision?: Record<string, any> | null;
        last_action?: Record<string, any> | null;
      };
      last_run?: Record<string, any> | null;
      recent_runs?: Array<Record<string, any>>;
      counts?: Record<string, number>;
      recent_actions: Array<Record<string, any>>;
    };
    standing_orders?: {
      status: string;
      counts: Record<string, number>;
      active_orders: Array<Record<string, any>>;
      due_orders: Array<Record<string, any>>;
      review_only_orders: Array<Record<string, any>>;
      recent_runs: Array<Record<string, any>>;
    };
    plugins?: {
      status: string;
      counts: Record<string, number>;
      restart_pending?: boolean;
      summary?: string;
      attention_summary?: string;
      latest_run_summary?: string;
      items: Array<Record<string, any>>;
      recent_runs: Array<Record<string, any>>;
    };
    channels?: {
      status: string;
      counts?: {
        linked_dms?: number;
        mapped_spaces?: number;
        connected_integrations?: number;
      };
      summary?: string;
      attention_summary?: string;
      platform_summaries?: Array<Record<string, any>>;
      owner_link_summaries?: Array<Record<string, any>>;
      runtime?: {
        connected_platforms?: string[];
        platforms?: Array<Record<string, any>>;
      };
      linked_dms?: Array<Record<string, any>>;
      mapped_spaces?: Array<Record<string, any>>;
    };
    nodes?: {
      status: string;
      counts: Record<string, number>;
      summary?: string;
      attention_summary?: string;
      preferred_notification_summary?: string;
      node_summaries?: Array<Record<string, any>>;
      recovery_summaries?: Array<Record<string, any>>;
      preferred_notification_node?: Record<string, any> | null;
      items: Array<Record<string, any>>;
      pending_pairings: Array<Record<string, any>>;
      recent_invocations: Array<Record<string, any>>;
      recent_artifacts: Array<Record<string, any>>;
      recent_recoveries?: Array<Record<string, any>>;
    };
    proactive: {
      status: string;
      controls: {
        can_check_now: boolean;
        can_pause: boolean;
        can_resume: boolean;
      };
      loop: {
        is_running: boolean;
        is_paused: boolean;
        loop_state: string;
        last_check_started_at?: number | null;
        last_check_completed_at?: number | null;
        last_error?: string;
        last_decision?: Record<string, any> | null;
        last_action?: Record<string, any> | null;
      };
      last_run?: Record<string, any> | null;
      recent_runs?: Array<Record<string, any>>;
      counts?: Record<string, number>;
      recent_actions: Array<Record<string, any>>;
    };
  };
};

type RuntimeUpdateEvent = {
  type: string;
  data?: {
    owner_root_key?: string;
    section?: string;
    payload?: any;
  };
};

type ThreadHistoryItem = {
  id?: string | number;
  role?: string;
  role_label?: string;
  content?: string;
  excerpt?: string;
  timestamp?: number;
  type?: string;
  response_to?: string;
  voice_turn_decision_trace?: Record<string, any>;
};

type RuntimeThreadHistoryResponse = {
  thread?: Record<string, any> | null;
  history?: {
    status?: string;
    items?: ThreadHistoryItem[];
    count?: number;
    limit?: number;
    has_more?: boolean;
    latest_timestamp?: number | null;
  } | null;
};

type MemoryPreviewData = {
  capability?: {
    mode?: string;
  };
  recent_captures?: Array<{
    item_id?: string;
    source_kind?: string;
    snippet?: string;
  }>;
  counts?: Record<string, number>;
};

type VoiceDraft = {
  voice_engine: string;
  interaction_mode: string;
  presence_profile: string;
  focus_state: string;
  local_transport_mode: string;
};

type VoiceRuntimeProfile = {
  voice_engine: string;
  interaction_mode: string;
  presence_profile: string;
  focus_state: string;
  local_transport_mode?: string;
};

const DEFAULT_VOICE_DRAFT: VoiceDraft = {
  voice_engine: "classic",
  interaction_mode: "direct_address",
  presence_profile: "balanced",
  focus_state: "responsive",
  local_transport_mode: "push_to_talk",
};

const LOCAL_VOICE_RUNTIME_DRAFT_KEY = "__local_voice_runtime__";

const THREAD_HISTORY_BATCH_SIZE = 12;
const THREAD_HISTORY_MAX_LIMIT = 60;

function formatTimestamp(timestamp?: number | null): string {
  if (!timestamp) return "Never";
  return new Date(timestamp * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatRelative(timestamp?: number | null): string {
  if (!timestamp) return "never";
  const deltaSeconds = Math.max(0, Math.round(Date.now() / 1000 - timestamp));
  if (deltaSeconds < 60) return `${deltaSeconds}s ago`;
  if (deltaSeconds < 3600) return `${Math.round(deltaSeconds / 60)}m ago`;
  return `${Math.round(deltaSeconds / 3600)}h ago`;
}

function warningTone(severity: string): string {
  if (severity === "error") return "border-red-400/25 bg-red-400/10 text-red-100";
  if (severity === "warning") return "border-amber-400/25 bg-amber-400/10 text-amber-100";
  return "border-white/10 bg-white/[0.04] text-white/75";
}

function statusTone(status: string): string {
  switch (status) {
    case "active":
      return "text-emerald-300";
    case "mixed":
    case "degraded":
    case "paused":
      return "text-amber-200";
    case "recent":
      return "text-white/75";
    case "error":
    case "stopped":
    case "inactive":
    case "disabled":
      return "text-red-200";
    default:
      return "text-white/75";
  }
}

function sameLlmActivity(a?: LLMRecentActivityEntry | null, b?: LLMRecentActivityEntry | null): boolean {
  return Boolean(a && b && a.log_context === b.log_context && a.recorded_at === b.recorded_at);
}

function formatLlmProviderModel(entry?: LLMRecentActivityEntry | null): string {
  if (!entry) return "Unknown";
  return [entry.provider_label || entry.provider || "", entry.effective_model_reference || entry.model || ""].filter(Boolean).join(" • ");
}

function formatLlmRouteReference(
  route?: {
    requested_provider_label?: string;
    requested_provider?: string;
    requested_model_reference?: string;
    effective_provider_label?: string;
    effective_provider?: string;
    effective_model_reference?: string;
  } | null,
  mode: "requested" | "effective" = "effective",
): string {
  if (!route) return "Unknown";
  if (mode === "requested") {
    return [route.requested_provider_label || route.requested_provider || "", route.requested_model_reference || ""]
      .filter(Boolean)
      .join(" | ");
  }
  return [
    route.effective_provider_label || route.effective_provider || route.requested_provider_label || route.requested_provider || "",
    route.effective_model_reference || route.requested_model_reference || "",
  ]
    .filter(Boolean)
    .join(" | ");
}

function formatLlmRouteSummary(
  route?: {
    requested_provider_label?: string;
    requested_provider?: string;
    requested_model_reference?: string;
    effective_provider_label?: string;
    effective_provider?: string;
    effective_model_reference?: string;
  } | null,
  mode: "requested" | "effective" = "effective",
  fallback = "Unconfigured",
): string {
  const summary = formatLlmRouteReference(route, mode);
  return summary || fallback;
}

function formatLlmActivity(entry?: LLMRecentActivityEntry | null): string {
  if (!entry) return "Unknown";
  const parts = [
    entry.lane_label || entry.activity_label || entry.log_context || "Reply",
    entry.provider_label || entry.provider || "",
    entry.effective_model_reference || entry.model || "",
  ].filter(Boolean);
  return parts.join(" • ");
}

function llmLaneStatus(lane?: LLMResponseLaneSummary | null): string {
  if (!lane?.last_response) return "idle";
  return lane.last_response.success === false ? "degraded" : "ready";
}

function formatRouteSource(source?: string | null): string {
  const clean = String(source || "").trim();
  if (!clean) return "";
  return clean.replaceAll("_", " ");
}

function formatJobLabel(job: Record<string, any>): string {
  const jobType = String(job.job_type || "job");
  const subtype = String(job.subtype || "");
  if (jobType === "deferred_action" && subtype) {
    return subtype.charAt(0).toUpperCase() + subtype.slice(1);
  }
  return jobType.replaceAll("_", " ");
}

function formatReason(reason?: string | null): string {
  const clean = String(reason || "").trim();
  if (!clean) return "";
  return clean.replaceAll("_", " ");
}

function formatAgentRootLabel(root?: Record<string, any> | null): string {
  const label = String(root?.label || root?.alias || root?.session_key || "").trim();
  return label || "Agent Root";
}

function formatAgentRootWorkspace(root?: Record<string, any> | null): string {
  const raw = String(root?.workspace_root || "").trim();
  if (!raw) return "";
  const normalized = raw.replaceAll("\\", "/").replace(/\/+$/, "");
  if (!normalized) return "";
  const parts = normalized.split("/");
  return parts[parts.length - 1] || normalized;
}

function formatAgentRootContext(root?: Record<string, any> | null): string {
  const parts: string[] = [];
  const platform = formatReason(String(root?.platform || "").trim());
  if (platform) {
    parts.push(platform.charAt(0).toUpperCase() + platform.slice(1));
  }
  const lane = String(root?.lane_label || root?.lane_kind || root?.lane_id || "").trim();
  if (lane) {
    parts.push(lane);
  }
  const workspace = formatAgentRootWorkspace(root);
  if (workspace) {
    parts.push(`workspace ${workspace}`);
  }
  const threadId = String(root?.thread_id || "").trim();
  if (!lane && threadId) {
    parts.push(`thread ${threadId}`);
  }
  return parts.join(" | ");
}

function resolveAgentRootForRun(
  run: Record<string, any> | null | undefined,
  rootsBySessionKey: Map<string, Record<string, any>>,
  rootsByRunId: Map<string, Record<string, any>>,
): Record<string, any> | null {
  if (!run) return null;
  const metadata = run.metadata && typeof run.metadata === "object" && !Array.isArray(run.metadata)
    ? run.metadata as Record<string, any>
    : {};
  const rootSessionKey = String(metadata.root_session_key || metadata.session_key || run.runtime_session_key || "").trim();
  const runId = String(run.run_id || "").trim();
  return rootsBySessionKey.get(rootSessionKey) || rootsByRunId.get(runId) || null;
}

function formatAgentRootActivity(
  root: Record<string, any> | null | undefined,
  runsById: Map<string, Record<string, any>>,
): string {
  if (!root) return "";
  const activeRunId = String(root.active_run_id || "").trim();
  if (activeRunId) {
    const run = runsById.get(activeRunId);
    const goal = String(run?.goal || run?.summary || "").trim();
    if (goal) return `Running: ${goal}`;
    return `Active run ${activeRunId.slice(0, 10)}`;
  }

  const lastCompletion = root.last_completion;
  if (lastCompletion && typeof lastCompletion === "object" && !Array.isArray(lastCompletion)) {
    const completion = lastCompletion as Record<string, any>;
    const summary = String(completion.summary || completion.response_summary || completion.response_text || "").trim();
    if (summary) return `Last completion: ${summary}`;
    const status = formatReason(String(completion.status || completion.response_status || "").trim());
    if (status) return `Last completion ${status}`;
  }

  const lastActivity = Number(root.last_activity || root.updated_at || 0);
  if (Number.isFinite(lastActivity) && lastActivity > 0) {
    return `Updated ${formatRelative(lastActivity)}`;
  }
  return "Ready to reopen";
}

function formatAgentConfirmationSummary(confirmation?: Record<string, any> | null): string {
  const command = String(confirmation?.command || "").trim();
  if (command) return command;
  const metadata = confirmation?.metadata && typeof confirmation.metadata === "object" && !Array.isArray(confirmation.metadata)
    ? confirmation.metadata as Record<string, any>
    : {};
  const prompt = String(metadata.summary || metadata.prompt || metadata.description || "").trim();
  return prompt || "Confirmation pending";
}

function formatSessionHistoryVoiceSummary(entry: Record<string, any>): string {
  const rawTrace = entry?.voice_turn_decision_trace;
  if (!rawTrace || typeof rawTrace !== "object" || Array.isArray(rawTrace)) return "";

  const trace = rawTrace as Record<string, any>;
  const parts = [String(trace.summary || trace.behavior_label || "").trim()].filter(Boolean);
  const mirrorPolicy = String(trace.mirror_policy_label || "").trim();
  if (mirrorPolicy) {
    parts.push(mirrorPolicy);
  }

  const identityCues = Array.isArray(trace.identity_cues)
    ? trace.identity_cues
        .map((cue) => {
          if (!cue || typeof cue !== "object" || Array.isArray(cue)) return "";
          const cueRecord = cue as Record<string, any>;
          const label = String(cueRecord.label || cueRecord.key || "").trim();
          const value = String(cueRecord.value_label || cueRecord.value || "").trim();
          if (label && value) return `${label} ${value}`;
          return value || label;
        })
        .filter(Boolean)
    : [];

  if (identityCues.length) {
    parts.push(`Identity ${identityCues.join(", ")}`);
  }

  return parts.join(" | ");
}

function formatRuntimeStatusLabel(status?: string | null): string {
  const clean = String(status || "").trim().toLowerCase();
  if (!clean) return "unknown";
  if (clean === "ready") return "stable";
  return formatReason(clean);
}

function summarizeBackendStartup(startup?: BackendStartupSnapshot | null): string {
  const cleanStatus = String(startup?.status || "").trim().toLowerCase();
  if (!cleanStatus || cleanStatus === "ready" || cleanStatus === "not_started") return "";
  const attentionStages = (startup?.stages || []).filter((stage) => {
    const stageStatus = String(stage?.status || "").trim().toLowerCase();
    return stageStatus === "degraded" || stageStatus === "failed";
  });
  if (attentionStages.length === 1) {
    const stage = attentionStages[0];
    return `Startup ${formatRuntimeStatusLabel(cleanStatus)} • ${String(stage.label || formatReason(stage.stage_id) || "stage").trim()}`;
  }
  if (attentionStages.length > 1) {
    return `Startup ${formatRuntimeStatusLabel(cleanStatus)} • ${attentionStages.length} stages need attention`;
  }
  const failedCount = Number(startup?.counts?.failed || 0);
  const degradedCount = Number(startup?.counts?.degraded || 0);
  if (failedCount > 0) {
    return `Startup ${formatRuntimeStatusLabel(cleanStatus)} • ${failedCount} failed stage${failedCount === 1 ? "" : "s"}`;
  }
  if (degradedCount > 0) {
    return `Startup ${formatRuntimeStatusLabel(cleanStatus)} • ${degradedCount} degraded stage${degradedCount === 1 ? "" : "s"}`;
  }
  return `Startup ${formatRuntimeStatusLabel(cleanStatus)}`;
}

function describeBackendStartup(startup?: BackendStartupSnapshot | null): string {
  const attentionStages = (startup?.stages || []).filter((stage) => {
    const stageStatus = String(stage?.status || "").trim().toLowerCase();
    return stageStatus === "degraded" || stageStatus === "failed";
  });
  const firstStageMessage = String(attentionStages[0]?.message || "").trim();
  if (firstStageMessage) return firstStageMessage;
  if (startup?.completed_at) return `Last startup ${formatRelative(Number(startup.completed_at))}`;
  if (startup?.started_at) return `Startup began ${formatRelative(Number(startup.started_at))}`;
  return "";
}

function formatVoiceEngineSummary(
  engine?:
    | {
        requested?: string;
        requested_label?: string;
        effective?: string;
        effective_label?: string;
      }
    | null,
  mode: "requested" | "effective" = "effective",
  fallback = "classic",
): string {
  if (mode === "requested") {
    return String(engine?.requested_label || engine?.requested || fallback);
  }
  return String(engine?.effective_label || engine?.effective || engine?.requested_label || engine?.requested || fallback);
}

type WorkflowAuditNotice = {
  state: string;
  label: string;
  detail: string;
  nextMove?: string;
  pendingCount?: number;
  toneClassName: string;
};

function asRecord(value: unknown): Record<string, any> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, any>;
  }
  return {};
}

function auditActorPhrase(event: Record<string, any>): string {
  const actorKind = String(event.actor_kind || "").trim().toLowerCase();
  if (actorKind === "owner") return "you";
  if (actorKind === "user") return "another user";
  if (actorKind === "system") return "the system";
  return "someone";
}

function workflowAuditToneClassName(state: string): string {
  switch (state) {
    case "approval_pending":
      return "border-amber-400/20 bg-amber-400/10";
    case "approval_denied":
    case "cancelled":
      return "border-red-400/20 bg-red-400/10";
    case "paused":
      return "border-white/10 bg-black/20";
    default:
      return "border-white/10 bg-black/20";
  }
}

function buildWorkflowAuditNotice(record?: Record<string, any> | null): WorkflowAuditNotice | null {
  const item = asRecord(record);
  const controlEvent = asRecord(item.last_control_event);
  const confirmationEvent = asRecord(item.last_confirmation_event);
  const confirmationState = asRecord(item.confirmation_state);
  const workflowStatus = String(item.status || item.workflow_status || "").trim().toLowerCase();
  const waitState = String(item.wait_state || "").trim().toLowerCase();
  const confirmationEventKind = String(confirmationEvent.event_kind || "").trim().toLowerCase();
  const confirmationStatus = String(confirmationState.status || "").trim().toLowerCase();
  const confirmationResolution = String(confirmationState.resolution || confirmationEvent.resolution || "").trim().toLowerCase();
  const confirmationCommand = String(
    confirmationState.confirmation_command || confirmationEvent.confirmation_command || "",
  ).trim();
  const reviewReason = String(item.audit_summary || item.review_reason || "").trim();
  const nextMoveText = String(item.next_move_text || "").trim();
  const waitingPrompt = String(item.waiting_for_input_prompt || "").trim();
  const pendingCountRaw = Number(confirmationState.pending_confirmation_count ?? confirmationEvent.pending_confirmation_count ?? 0);
  const pendingCount = Number.isFinite(pendingCountRaw) && pendingCountRaw > 0 ? pendingCountRaw : 0;

  let auditState = String(item.audit_state || "").trim().toLowerCase();
  if (!auditState) {
    if (
      confirmationResolution === "denied" ||
      confirmationEventKind === "confirmation_denied"
    ) {
      auditState = "approval_denied";
    } else if (
      confirmationStatus === "waiting_confirmation" ||
      waitState === "confirmation" ||
      confirmationEventKind === "confirmation_requested"
    ) {
      auditState = "approval_pending";
    } else if (workflowStatus === "paused" || String(controlEvent.event_kind || "").trim().toLowerCase() === "manual_pause") {
      auditState = "paused";
    } else if (
      workflowStatus === "cancelled" ||
      ["manual_cancel", "manual_action_cancel"].includes(String(controlEvent.event_kind || "").trim().toLowerCase())
    ) {
      auditState = "cancelled";
    }
  }

  if (!["approval_pending", "approval_denied", "paused", "cancelled"].includes(auditState)) {
    return null;
  }

  if (auditState === "approval_pending") {
    const detail =
      reviewReason ||
      (confirmationCommand
        ? `Waiting for approval to run ${confirmationCommand}.`
        : "Waiting for approval before continuing.");
    return {
      state: auditState,
      label: "Approval Pending",
      detail,
      nextMove:
        nextMoveText ||
        waitingPrompt ||
        (confirmationCommand ? `Approve the pending confirmation to run ${confirmationCommand}` : "Approve the pending confirmation"),
      pendingCount,
      toneClassName: workflowAuditToneClassName(auditState),
    };
  }

  if (auditState === "approval_denied") {
    const detail =
      reviewReason ||
      (confirmationCommand ? `Approval was denied for ${confirmationCommand}.` : "Approval was denied.");
    return {
      state: auditState,
      label: "Approval Denied",
      detail,
      nextMove: nextMoveText,
      pendingCount,
      toneClassName: workflowAuditToneClassName(auditState),
    };
  }

  if (auditState === "paused") {
    return {
      state: auditState,
      label: "Paused",
      detail: reviewReason || `Paused by ${auditActorPhrase(controlEvent)}.`,
      nextMove: nextMoveText || waitingPrompt || "Resume this workflow when ready.",
      toneClassName: workflowAuditToneClassName(auditState),
    };
  }

  return {
    state: auditState,
    label: "Cancelled",
    detail: reviewReason || `Cancelled by ${auditActorPhrase(controlEvent)}.`,
    nextMove: nextMoveText,
    toneClassName: workflowAuditToneClassName(auditState),
  };
}

function WorkflowAuditCallout({
  notice,
  title = "Audit State",
  className = "",
}: {
  notice?: WorkflowAuditNotice | null;
  title?: string;
  className?: string;
}) {
  if (!notice) return null;

  return (
    <div className={`${className} rounded-lg border px-3 py-2 text-xs ${notice.toneClassName}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-white/45">{title}</div>
        <div className="text-white/65">{notice.label}</div>
      </div>
      <div className="mt-1 text-white/85">{notice.detail}</div>
      {notice.pendingCount && notice.pendingCount > 1 ? (
        <div className="mt-1 text-white/55">{notice.pendingCount} confirmations are still pending.</div>
      ) : null}
      {notice.nextMove ? <div className="mt-1 text-white/60">Next move: {notice.nextMove}</div> : null}
    </div>
  );
}

function dedupeActionLinks(actions: MissionControlActionLink[]): MissionControlActionLink[] {
  const seen = new Set<string>();
  return actions.filter((action) => {
    const key = `${action.label}:${action.href}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function buildMissionWarningActions(warning?: RuntimeWarning | null): MissionControlActionLink[] {
  const code = String(warning?.code || "").trim().toLowerCase();
  if (!code) return [];
  if (["voice_surface_engine_config_fallbacks", "voice_engine_fallback_active", "browser_direct_realtime_unavailable", "voice_bootstrap_failed"].includes(code)) {
    return [
      { label: "Review voice runtime", href: VOICE_RUNTIME_SECTION_HREF },
    ];
  }
  if (["llm_reply_surface_route_changed", "llm_route_errors", "llm_route_warnings"].includes(code)) {
    return [{ label: "Review model routing", href: RUNTIME_HEALTH_SECTION_HREF }];
  }
  if (
    ["backend_startup_attention", "launcher_core_blockers", "launcher_warnings", "prompt_readiness_warning", "surface_reliability_attention"].includes(code)
    || code.startsWith("backend_startup_")
  ) {
    return [{ label: "Review runtime health", href: RUNTIME_HEALTH_SECTION_HREF }];
  }
  return [];
}

function describeReplySurfaceAttention(surface?: ReplySurfaceAttentionInput | null): string {
  if (!surface) return "";
  const missingRequirements = (surface.missing_requirements || []).filter(Boolean);
  if (missingRequirements.length) {
    const helper = missingRequirements.length === 1 ? "is" : "are";
    return `Blocked until ${missingRequirements.join(", ")} ${helper} configured.`;
  }
  if (surface.engine?.fallback_active) {
    const requested = surface.engine.requested_label || surface.engine.requested || "requested engine";
    const effective = surface.engine.effective_label || surface.engine.effective || "fallback engine";
    const reason = surface.engine.fallback_reason_label || formatReason(surface.engine.fallback_reason);
    return `Configured for ${requested}; starting on ${effective}${reason ? ` because ${reason}` : ""}.`;
  }
  const requestedRoute = formatLlmRouteReference(surface, "requested");
  const effectiveRoute = formatLlmRouteReference(surface);
  if (surface.route_changed && requestedRoute && effectiveRoute && requestedRoute !== effectiveRoute) {
    return `Configured for ${requestedRoute}; current runtime resolves to ${effectiveRoute}.`;
  }
  if (surface.issue?.message) return String(surface.issue.message);
  return "";
}

function replySurfaceControlLinks(surface?: ReplySurfaceAttentionInput | null): MissionControlActionLink[] {
  if (!surface) return [];
  const actions: MissionControlActionLink[] = [];
  const isVoiceLane = surface.family === "voice" || surface.routing_kind === "direct_openai_realtime";
  if (surface.route_changed || surface.issue?.message || (surface.missing_requirements || []).length > 0) {
    actions.push({
      label: isVoiceLane ? "Review voice runtime" : "Review model routing",
      href: isVoiceLane ? VOICE_RUNTIME_SECTION_HREF : RUNTIME_HEALTH_SECTION_HREF,
    });
  }
  return dedupeActionLinks(actions);
}

function describeBrowserVoicePathAttention(path?: BrowserVoicePathAttentionInput | null): string {
  if (!path) return "";
  const missingRequirements = (path.missing_requirements || []).filter(Boolean);
  if (missingRequirements.length) {
    const helper = missingRequirements.length === 1 ? "is" : "are";
    return `Unavailable until ${missingRequirements.join(", ")} ${helper} configured.`;
  }
  if (path.fallback_active) {
    const requested = path.requested_engine_label || path.requested_engine || "requested engine";
    const effective = path.effective_engine_label || path.effective_engine || "fallback engine";
    const reason = path.fallback_reason_label || formatReason(path.fallback_reason);
    return `Configured for ${requested}; starting on ${effective}${reason ? ` because ${reason}` : ""}.`;
  }
  return "";
}

function browserVoicePathControlLinks(path?: BrowserVoicePathAttentionInput | null): MissionControlActionLink[] {
  if (!path) return [];
  const actions: MissionControlActionLink[] = [];
  if ((path.missing_requirements || []).length > 0 || path.fallback_active) {
    actions.push({ label: "Review voice runtime", href: VOICE_RUNTIME_SECTION_HREF });
  }
  return dedupeActionLinks(actions);
}

function describeVoiceSurfaceEngineAttention(surface?: VoiceSurfaceEngineAttentionInput | null): string {
  if (!surface?.fallback_active) return "";
  const requested = surface.requested_engine_label || surface.requested_engine || "requested engine";
  const effective = surface.effective_engine_label || surface.effective_engine || "fallback engine";
  const reason = surface.fallback_reason_label || formatReason(surface.fallback_reason);
  return `Configured for ${requested}; starting on ${effective}${reason ? ` because ${reason}` : ""}.`;
}

function voiceSurfaceEngineControlLinks(surface?: VoiceSurfaceEngineAttentionInput | null): MissionControlActionLink[] {
  if (!surface) return [];
  const actions: MissionControlActionLink[] = [];
  if (surface.fallback_active) {
    actions.push({ label: "Review voice runtime", href: VOICE_RUNTIME_SECTION_HREF });
  }
  return dedupeActionLinks(actions);
}

function formatSupportedVoiceEngines(
  supportedEngines?: Array<{ id: string; label: string }> | null,
  fallback = "Classic",
): string {
  const labels = (supportedEngines || []).map((engine) => String(engine.label || "").trim()).filter(Boolean);
  return labels.length ? labels.join(" | ") : fallback;
}

function formatModelVoiceSummary(model?: string | null, voice?: string | null, fallback = "Unconfigured"): string {
  const parts = [String(model || "").trim(), String(voice || "").trim() ? `voice ${String(voice || "").trim()}` : ""].filter(Boolean);
  return parts.length ? parts.join(" | ") : fallback;
}

function formatRuntimeLabelList(labels: string[]): string {
  const cleanLabels = labels.map((label) => String(label || "").trim()).filter(Boolean);
  if (!cleanLabels.length) return "";
  if (cleanLabels.length === 1) return cleanLabels[0];
  if (cleanLabels.length === 2) return `${cleanLabels[0]} and ${cleanLabels[1]}`;
  return `${cleanLabels.slice(0, -1).join(", ")}, and ${cleanLabels[cleanLabels.length - 1]}`;
}

function formatVoiceRuntimeProfileSummary(profile?: VoiceRuntimeProfile | null): string {
  if (!profile) return "Unconfigured";
  const parts = [
    profile.voice_engine,
    profile.interaction_mode,
    profile.presence_profile,
    profile.focus_state,
    profile.local_transport_mode,
  ]
    .map((value) => formatReason(value))
    .filter(Boolean);
  return parts.length ? parts.join(" | ") : "Unconfigured";
}

function collectVoiceRuntimeProfileDifferences(
  configured?: VoiceRuntimeProfile | null,
  running?: VoiceRuntimeProfile | null,
): string[] {
  if (!configured || !running) return [];

  const fieldSpecs: Array<{ key: keyof VoiceRuntimeProfile; label: string }> = [
    { key: "voice_engine", label: "engine" },
    { key: "interaction_mode", label: "interaction" },
    { key: "presence_profile", label: "presence" },
    { key: "focus_state", label: "focus" },
  ];

  if (configured.local_transport_mode || running.local_transport_mode) {
    fieldSpecs.push({ key: "local_transport_mode", label: "local mode" });
  }

  return fieldSpecs
    .filter(({ key }) => {
      const configuredValue = String(configured[key] || "").trim().toLowerCase();
      const runningValue = String(running[key] || "").trim().toLowerCase();
      return configuredValue !== runningValue;
    })
    .map(({ label }) => label);
}

type ControlSurfaceCardProps = {
  title: string;
  status: string;
  configuredValue: string;
  runningValue: string;
  configuredLabel?: string;
  runningLabel?: string;
  summary?: string;
  attention?: string;
  details?: ReactNode;
  actions?: MissionControlActionLink[];
};

function ControlSurfaceCard({
  title,
  status,
  configuredValue,
  runningValue,
  configuredLabel = "Configured",
  runningLabel = "Running",
  summary,
  attention,
  details,
  actions = [],
}: ControlSurfaceCardProps) {
  return (
    <div className="rounded-xl border border-white/5 bg-black/20 px-3 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-white/80">{title}</span>
        <span className={statusTone(status)}>{formatRuntimeStatusLabel(status)}</span>
      </div>
      {summary ? <div className="mt-1 text-white/55">{summary}</div> : null}
      <div className="mt-3 grid gap-2 text-[11px] sm:grid-cols-2">
        <div className="rounded-lg border border-white/5 bg-white/[0.03] px-2.5 py-2">
          <div className="text-white/40">{configuredLabel}</div>
          <div className="mt-1 break-words text-white/80">{configuredValue}</div>
        </div>
        <div className="rounded-lg border border-white/5 bg-white/[0.03] px-2.5 py-2">
          <div className="text-white/40">{runningLabel}</div>
          <div className="mt-1 break-words text-white/80">{runningValue}</div>
        </div>
      </div>
      {attention ? (
        <div className="mt-2 rounded-lg border border-amber-400/15 bg-amber-400/10 px-2.5 py-2 text-[11px] text-amber-100/80">
          {attention}
        </div>
      ) : null}
      {details ? <div className="mt-2 space-y-1 text-[11px] text-white/50">{details}</div> : null}
      {actions.length ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {actions.map((action) => (
            <Link
              key={`${title}:${action.href}:${action.label}`}
              href={action.href}
              className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-white/75 transition-colors hover:bg-white/10"
            >
              {action.label}
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function formatContextState(value?: string | null): string {
  const clean = String(value || "").trim();
  if (!clean) return "unknown";
  return clean.replaceAll("_", " ");
}

function voiceSessionSurfaceLabel(session: Record<string, any>): string {
  const platform = String(session.platform || "").trim().toLowerCase();
  if (platform === "local") return "Local Voice";
  if (platform === "web_local" || platform === "browser" || platform === "web") return "Browser Voice";
  const channelId = session.channel_id ?? "";
  return `Discord Voice ${channelId}`.trim();
}

function SectionCard({
  icon,
  title,
  status,
  children,
  anchorId,
}: {
  icon: ReactNode;
  title: string;
  status: string;
  children: ReactNode;
  anchorId?: string;
}) {
  return (
    <section id={anchorId} className="scroll-mt-24 rounded-2xl border border-white/5 bg-white/[0.03] p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-white/85">
          <div className="rounded-lg bg-white/5 p-1.5 text-white/70">{icon}</div>
          <h3 className="text-sm font-medium">{title}</h3>
        </div>
        <span className={`text-xs font-mono uppercase tracking-widest ${statusTone(status)}`}>{status}</span>
      </div>
      {children}
    </section>
  );
}

export function RuntimeMissionCard() {
  const { toast } = useToast();
  // First door, task 4 (2026-09-17): "Manage channels" points at the SaaS-era
  // account page, which a self-hosted install hides.
  const showOwnerControls = useLocalMode() === false;
  const [mission, setMission] = useState<RuntimeMission | null>(null);
  const [memoryPreview, setMemoryPreview] = useState<MemoryPreviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [expandedThreadHistoryId, setExpandedThreadHistoryId] = useState<string | null>(null);
  const [threadHistoryById, setThreadHistoryById] = useState<Record<string, RuntimeThreadHistoryResponse>>({});
  const [threadHistoryErrors, setThreadHistoryErrors] = useState<Record<string, string>>({});
  const [threadHistoryBusyKey, setThreadHistoryBusyKey] = useState<string | null>(null);
  const [voiceDrafts, setVoiceDrafts] = useState<Record<string, VoiceDraft>>({});
  const [workflowAnswerDrafts, setWorkflowAnswerDrafts] = useState<Record<string, string>>({});

  const syncVoiceDrafts = useCallback((voiceSection?: RuntimeMission["sections"]["voice"] | null) => {
    if (!voiceSection) return;
    setVoiceDrafts((current) => {
      const next = { ...current };
      const activeLocalSession = voiceSection.active_local_session || null;
      next[LOCAL_VOICE_RUNTIME_DRAFT_KEY] = {
        voice_engine: String(
          activeLocalSession?.voice_engine_requested
          || activeLocalSession?.voice_engine
          || voiceSection.saved_defaults?.voice_engine
          || DEFAULT_VOICE_DRAFT.voice_engine,
        ),
        interaction_mode: String(
          activeLocalSession?.interaction_mode
          || voiceSection.saved_defaults?.interaction_mode
          || DEFAULT_VOICE_DRAFT.interaction_mode,
        ),
        presence_profile: String(
          activeLocalSession?.presence_profile
          || voiceSection.saved_defaults?.presence_profile
          || DEFAULT_VOICE_DRAFT.presence_profile,
        ),
        focus_state: String(
          activeLocalSession?.focus_state
          || voiceSection.saved_defaults?.focus_state
          || DEFAULT_VOICE_DRAFT.focus_state,
        ),
        local_transport_mode: String(
          activeLocalSession?.local_transport_mode
          || voiceSection.saved_defaults?.local_transport_mode
          || DEFAULT_VOICE_DRAFT.local_transport_mode,
        ),
      };
      for (const session of voiceSection.sessions || []) {
        const sessionKey = String(session.runtime_session_key || "");
        if (!sessionKey) continue;
        next[sessionKey] = {
          voice_engine: String(
            session.voice_engine_requested
            || session.voice_engine
            || voiceSection.saved_defaults?.voice_engine
            || DEFAULT_VOICE_DRAFT.voice_engine,
          ),
          interaction_mode: String(session.interaction_mode || voiceSection.saved_defaults?.interaction_mode || DEFAULT_VOICE_DRAFT.interaction_mode),
          presence_profile: String(session.presence_profile || voiceSection.saved_defaults?.presence_profile || DEFAULT_VOICE_DRAFT.presence_profile),
          focus_state: String(session.focus_state || voiceSection.saved_defaults?.focus_state || DEFAULT_VOICE_DRAFT.focus_state),
          local_transport_mode: String(session.local_transport_mode || voiceSection.saved_defaults?.local_transport_mode || DEFAULT_VOICE_DRAFT.local_transport_mode),
        };
      }
      return next;
    });
  }, []);

  const fetchMission = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = Boolean(opts?.silent);
    if (!silent) setLoading(true);
    setRefreshing(silent);
    try {
      const [missionResult, memoryResult] = await Promise.allSettled([
        apiGet<RuntimeMission>("/api/web/runtime/mission"),
        apiGet<MemoryPreviewData>("/api/web/account/memory/overview"),
      ]);
      if (missionResult.status !== "fulfilled") {
        throw missionResult.reason;
      }
      const data = missionResult.value;
      setMission(data);
      if (memoryResult.status === "fulfilled") {
        setMemoryPreview(memoryResult.value);
      }
      syncVoiceDrafts(data.sections.voice);
      setError(null);
    } catch (e) {
      devLogError("Failed to load mission control snapshot", e);
      setError(e instanceof Error ? e.message : "Failed to load mission control.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [syncVoiceDrafts]);

  useEffect(() => {
    fetchMission();
  }, [fetchMission]);

  useEffect(() => {
    syncVoiceDrafts(mission?.sections.voice);
  }, [mission?.sections.voice, syncVoiceDrafts]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      ws = new WebSocket(getBackendWebSocketUrl("/api/chat/ws/stream"));

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as RuntimeUpdateEvent;
          if (payload.type !== "runtime_update") return;
          const update = payload.data;
          if (!update?.section || !update?.payload) return;
          const section = update.section as keyof RuntimeMission["sections"];
          if (!["health", "sessions", "jobs", "runs", "agent", "voice", "context", "ambient", "operator", "reminders", "heartbeat", "standing_orders", "plugins", "channels", "nodes", "proactive"].includes(String(section))) return;
          setMission((current) => {
            if (!current) return current;
            if (update.owner_root_key && update.owner_root_key !== current.owner_root_key) return current;
            const nextMission = {
              ...current,
              sections: {
                ...current.sections,
                [section]: update.payload,
              },
              generated_at: Date.now() / 1000,
            } as RuntimeMission;
            return nextMission;
          });
        } catch (e) {
          devLogError("Failed to parse runtime websocket update", e);
        }
      };

      ws.onclose = () => {
        reconnectTimer = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        ws?.close();
      };
    };

    connect();
    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, []);

  const loadThreadHistory = useCallback(
    async (threadId: string, limit = THREAD_HISTORY_BATCH_SIZE) => {
      const cleanThreadId = String(threadId || "").trim();
      if (!cleanThreadId) return;

      const normalizedLimit = Math.max(1, Math.min(Number(limit || THREAD_HISTORY_BATCH_SIZE), THREAD_HISTORY_MAX_LIMIT));
      const busyKey = `${cleanThreadId}:${normalizedLimit}`;

      setThreadHistoryBusyKey(busyKey);
      setThreadHistoryErrors((current) => {
        const next = { ...current };
        delete next[cleanThreadId];
        return next;
      });

      try {
        const detail = await apiGet<RuntimeThreadHistoryResponse>(
          `/api/web/runtime/threads/${encodeURIComponent(cleanThreadId)}/history?limit=${normalizedLimit}`,
        );
        setThreadHistoryById((current) => ({ ...current, [cleanThreadId]: detail }));
        setExpandedThreadHistoryId(cleanThreadId);
      } catch (e) {
        devLogError(`Failed to load thread history for ${cleanThreadId}`, e);
        const message = e instanceof Error ? e.message : "Failed to load thread history.";
        setThreadHistoryErrors((current) => ({ ...current, [cleanThreadId]: message }));
        toast({
          title: "Mission Control",
          message,
          variant: "error",
        });
      } finally {
        setThreadHistoryBusyKey((current) => (current === busyKey ? null : current));
      }
    },
    [toast],
  );

  const runtime = mission?.sections.health.runtime;
  const health = mission?.sections.health;
  const backendStartupSummary = summarizeBackendStartup(health?.backend?.startup);
  const backendStartupDetail = describeBackendStartup(health?.backend?.startup);
  const backendMissionControlSummary = String(health?.backend?.mission_control_summary || "").trim();
  const backendMissionControlDetail = String(health?.backend?.mission_control_detail || "").trim();
  const frontendMissionControlSummary = String(health?.frontend?.mission_control_summary || "").trim();
  const frontendMissionControlDetail = String(health?.frontend?.mission_control_detail || "").trim();
  const doctorMissionControlSummary = String(health?.doctor?.mission_control_summary || "").trim();
  const doctorMissionControlDetail = String(health?.doctor?.mission_control_detail || "").trim();
  const databaseMissionControlSummary = String(health?.database?.mission_control_summary || "").trim();
  const databaseMissionControlDetail = String(health?.database?.mission_control_detail || "").trim();
  const sessions = mission?.sections.sessions;
  const jobs = mission?.sections.jobs;
  const runs = mission?.sections.runs;
  const agent = mission?.sections.agent;
  const voice = mission?.sections.voice;
  const context = mission?.sections.context;
  const ambient = mission?.sections.ambient;
  const operator = mission?.sections.operator;
  const currentWorkflow = operator?.current_workflow ?? null;
  const currentWorkflowAudit = buildWorkflowAuditNotice(currentWorkflow);
  const reminders = mission?.sections.reminders;
  const heartbeat = mission?.sections.heartbeat ?? mission?.sections.proactive;
  const standingOrders = mission?.sections.standing_orders;
  const plugins = mission?.sections.plugins;
  const channels = mission?.sections.channels;
  const nodes = mission?.sections.nodes;
  const surfaceReliabilitySummary = String(health?.surface_reliability?.summary || "").trim();
  const surfaceReliabilityAttention = String(health?.surface_reliability?.attention_summary || "").trim();
  const surfaceReliabilityLatestEvent = String(health?.surface_reliability?.latest_event_summary || "").trim();
  const surfaceReliabilityCountsLine = `Recoveries ${Number(health?.surface_reliability?.counts?.recovery_attempts || 0)} • Fallbacks ${Number(health?.surface_reliability?.counts?.fallback_events || 0)} • Degraded ${Number(health?.surface_reliability?.counts?.degraded_events || 0)}`;
  const surfaceReliabilityDetailLine = surfaceReliabilityAttention
    ? surfaceReliabilityCountsLine
    : surfaceReliabilityLatestEvent && surfaceReliabilityLatestEvent !== surfaceReliabilitySummary
      ? surfaceReliabilityLatestEvent
      : "";
  const backendCardSummary = backendMissionControlSummary || backendStartupSummary || String(health?.backend?.status || "unknown");
  const backendCardDetail = backendMissionControlDetail || backendStartupDetail || String(health?.backend?.url || "");
  const frontendCardSummary = frontendMissionControlSummary || String(health?.frontend?.status || "unknown");
  const frontendCardDetail = frontendMissionControlDetail || String(health?.frontend?.url || "");
  const doctorCardSummary = doctorMissionControlSummary || String(health?.doctor?.status || "unknown");
  const doctorCardDetail = doctorMissionControlDetail
    || `Blockers ${Number(health?.doctor?.counts?.core_blockers || 0)} â€¢ Warnings ${Number(health?.doctor?.counts?.warnings || 0)}`;
  const databaseCardSummary = databaseMissionControlSummary
    || (health?.database?.writable ? "SQLite database is writable." : "SQLite database needs attention.");
  const databaseCardDetail = databaseMissionControlDetail || String(health?.database?.absolute_path || "");

  const liveVoiceSessions = voice?.sessions || [];
  const agentRoots = agent?.roots || [];
  const agentLiveRuns = agent?.live_runs || [];
  const agentDeliveries = agent?.deliveries || [];
  const agentConfirmations = agent?.confirmations || [];
  const agentRootsBySessionKey = new Map<string, Record<string, any>>();
  const agentRootsByRunId = new Map<string, Record<string, any>>();
  const agentRunsById = new Map<string, Record<string, any>>();
  agentRoots.forEach((root) => {
    const sessionKey = String(root?.session_key || "").trim();
    if (sessionKey) {
      agentRootsBySessionKey.set(sessionKey, root);
    }
    const activeRunId = String(root?.active_run_id || "").trim();
    if (activeRunId) {
      agentRootsByRunId.set(activeRunId, root);
    }
  });
  agentLiveRuns.forEach((run) => {
    const runId = String(run?.run_id || "").trim();
    if (runId) {
      agentRunsById.set(runId, run);
    }
  });
  const llmTaskRoutingEntries = Object.entries(runtime?.llm?.task_routing || {});
  const llmResponseRoute = runtime?.llm?.task_routing?.response;
  const llmRouteIssues = runtime?.llm?.route_issues || [];
  const llmRecentActivity = runtime?.llm?.recent_activity;
  const llmLastObservedCall = llmRecentActivity?.last_call;
  const llmLastObservedResponse = llmRecentActivity?.last_response;
  const llmLastActualResponse = llmRecentActivity?.last_successful_response;
  const llmLastFailedResponse = llmRecentActivity?.last_failed_response;
  const llmResponseLanes = llmRecentActivity?.response_lanes || [];
  const llmResponseLaneById = Object.fromEntries(
    llmResponseLanes.map((lane) => [lane.lane, lane]),
  ) as Record<string, LLMResponseLaneSummary>;
  const llmGlobalFailedResponse = llmRecentActivity?.global_last_failed_response;
  const llmReplySurfaces = runtime?.llm?.reply_surfaces || [];
  const llmPrimaryResponse = llmLastObservedResponse || llmLastActualResponse;
  const llmPrimaryResponseFailed = llmPrimaryResponse?.success === false;
  const llmReplyLaneDriftCount = llmReplySurfaces.filter((surface) => Boolean(surface.route_changed)).length;
  const llmRouteExceptionEntries = llmTaskRoutingEntries.filter(
    ([task, route]) => task !== "response" && (Boolean(route?.route_changed) || Boolean(route?.issue?.message)),
  );
  const voiceSurfaceEngines = runtime?.voice?.surface_engines || [];
  const browserVoicePaths = runtime?.voice?.browser_paths || [];
  const nativeBrowserVoicePath = browserVoicePaths.find((path) => path.id === "native_palli");
  const directBrowserVoicePath = browserVoicePaths.find((path) => path.id === "openai_realtime");
  const browserVoiceAttentionCount = browserVoicePaths.filter((path) => Boolean(describeBrowserVoicePathAttention(path))).length;
  const llmMissionControlSummary = String(runtime?.llm?.mission_control_summary || "").trim();
  const llmMissionControlDetail = String(runtime?.llm?.mission_control_detail || "").trim();
  const llmMissionControlAttention = String(runtime?.llm?.mission_control_attention || "").trim();
  const voiceMissionControlSummary = String(runtime?.voice?.mission_control_summary || "").trim();
  const voiceMissionControlDetail = String(runtime?.voice?.mission_control_detail || "").trim();
  const voiceMissionControlAttention = String(runtime?.voice?.mission_control_attention || "").trim();
  const browserVoiceActivity = voice?.browser_activity;
  const browserVoiceActivityById = Object.fromEntries(
    (browserVoiceActivity?.paths || []).map((item) => [item.id, item]),
  );
  const browserVoiceSessions = liveVoiceSessions.filter((session) => {
    const platform = String(session.platform || "").trim().toLowerCase();
    return platform === "web_local" || platform === "browser" || platform === "web";
  });
  const fallbackVoiceSessions = liveVoiceSessions.filter((session) => Boolean(session.voice_engine_fallback_active));
  const activeLocalVoiceSession = voice?.active_local_session || liveVoiceSessions.find((session) => {
    const platform = String(session.platform || "").trim().toLowerCase();
    return platform === "local";
  }) || null;
  const localVoiceActive = Boolean(activeLocalVoiceSession);
  const localVoiceSurfaceEngine = voiceSurfaceEngines.find((surface) => {
    const platform = String(surface.platform || "").trim().toLowerCase();
    return platform === "local";
  });
  const localVoiceDraft = voiceDrafts[LOCAL_VOICE_RUNTIME_DRAFT_KEY] || {
    voice_engine: String(
      activeLocalVoiceSession?.voice_engine_requested
      || activeLocalVoiceSession?.voice_engine
      || voice?.saved_defaults?.voice_engine
      || DEFAULT_VOICE_DRAFT.voice_engine,
    ),
    interaction_mode: String(
      activeLocalVoiceSession?.interaction_mode
      || voice?.saved_defaults?.interaction_mode
      || DEFAULT_VOICE_DRAFT.interaction_mode,
    ),
    presence_profile: String(
      activeLocalVoiceSession?.presence_profile
      || voice?.saved_defaults?.presence_profile
      || DEFAULT_VOICE_DRAFT.presence_profile,
    ),
    focus_state: String(
      activeLocalVoiceSession?.focus_state
      || voice?.saved_defaults?.focus_state
      || DEFAULT_VOICE_DRAFT.focus_state,
    ),
    local_transport_mode: String(
      activeLocalVoiceSession?.local_transport_mode
      || voice?.saved_defaults?.local_transport_mode
      || DEFAULT_VOICE_DRAFT.local_transport_mode,
    ),
  };
  const savedLocalVoiceProfile: VoiceRuntimeProfile = {
    voice_engine: String(voice?.saved_defaults?.voice_engine || DEFAULT_VOICE_DRAFT.voice_engine),
    interaction_mode: String(voice?.saved_defaults?.interaction_mode || DEFAULT_VOICE_DRAFT.interaction_mode),
    presence_profile: String(voice?.saved_defaults?.presence_profile || DEFAULT_VOICE_DRAFT.presence_profile),
    focus_state: String(voice?.saved_defaults?.focus_state || DEFAULT_VOICE_DRAFT.focus_state),
    local_transport_mode: String(voice?.saved_defaults?.local_transport_mode || DEFAULT_VOICE_DRAFT.local_transport_mode),
  };
  const activeLocalVoiceProfile = activeLocalVoiceSession
    ? {
        voice_engine: String(
          activeLocalVoiceSession.voice_engine
          || activeLocalVoiceSession.voice_engine_requested
          || savedLocalVoiceProfile.voice_engine,
        ),
        interaction_mode: String(activeLocalVoiceSession.interaction_mode || savedLocalVoiceProfile.interaction_mode),
        presence_profile: String(activeLocalVoiceSession.presence_profile || savedLocalVoiceProfile.presence_profile),
        focus_state: String(activeLocalVoiceSession.focus_state || savedLocalVoiceProfile.focus_state),
        local_transport_mode: String(activeLocalVoiceSession.local_transport_mode || savedLocalVoiceProfile.local_transport_mode || ""),
      }
    : null;
  const localVoiceDraftProfile: VoiceRuntimeProfile = {
    voice_engine: localVoiceDraft.voice_engine,
    interaction_mode: localVoiceDraft.interaction_mode,
    presence_profile: localVoiceDraft.presence_profile,
    focus_state: localVoiceDraft.focus_state,
    local_transport_mode: localVoiceDraft.local_transport_mode,
  };
  const localVoiceSavedDrift = collectVoiceRuntimeProfileDifferences(savedLocalVoiceProfile, activeLocalVoiceProfile);
  const localVoicePendingChanges = collectVoiceRuntimeProfileDifferences(
    activeLocalVoiceProfile || savedLocalVoiceProfile,
    localVoiceDraftProfile,
  );
  const localVoiceEffectiveEngine = String(
    activeLocalVoiceSession?.voice_engine_resolution?.effective_label
    || activeLocalVoiceSession?.voice_engine
    || savedLocalVoiceProfile.voice_engine,
  );
  const localVoiceRequestedEngine = String(
    activeLocalVoiceSession?.voice_engine_resolution?.requested_label
    || activeLocalVoiceSession?.voice_engine_requested
    || savedLocalVoiceProfile.voice_engine,
  );
  const localVoiceFallbackReason = String(
    activeLocalVoiceSession?.voice_engine_resolution?.fallback_reason_label
    || formatReason(activeLocalVoiceSession?.voice_engine_fallback_reason)
    || "",
  );
  const localVoiceStatus = activeLocalVoiceSession?.voice_engine_fallback_active
    ? "degraded"
    : localVoiceActive
      ? "active"
      : "idle";
  const localVoiceAttention = activeLocalVoiceSession?.voice_engine_fallback_active
    ? `Configured for ${localVoiceRequestedEngine}; running ${localVoiceEffectiveEngine}${localVoiceFallbackReason ? ` because ${localVoiceFallbackReason}` : ""}.`
    : localVoiceSavedDrift.length
      ? `Live local talk mode differs from saved defaults on ${formatRuntimeLabelList(localVoiceSavedDrift)}.`
      : "";
  const activeLocalVoiceDisplayProfile = activeLocalVoiceProfile
    ? {
        ...activeLocalVoiceProfile,
        voice_engine: localVoiceEffectiveEngine,
      }
    : null;
  const localVoiceEngineOptions = (() => {
    const options = (localVoiceSurfaceEngine?.supported_engines || []).map((item) => ({
      id: String(item.id || ""),
      label: String(item.label || item.id || ""),
    })).filter((item) => item.id);
    if (options.length) return options;

    const fallbackIds = [
      localVoiceDraft.voice_engine,
      String(activeLocalVoiceSession?.voice_engine_requested || ""),
      String(voice?.saved_defaults?.voice_engine || ""),
      "classic",
    ];
    const seen = new Set<string>();
    return fallbackIds
      .map((value) => String(value || "").trim())
      .filter((value) => {
        if (!value || seen.has(value)) return false;
        seen.add(value);
        return true;
      })
      .map((value) => ({ id: value, label: formatReason(value) }));
  })();
  const replyBrainCardSummary = llmMissionControlSummary
    || formatLlmRouteSummary(
      llmResponseRoute,
      "effective",
      runtime?.llm?.default_model_reference || "default response route",
    );
  const replyBrainCardDetail = llmMissionControlDetail
    || (llmPrimaryResponse
      ? `Running ${formatLlmProviderModel(llmPrimaryResponse)}`
      : "No reply activity observed in this process yet");
  const replyBrainCardAttention = llmMissionControlAttention
    || (llmPrimaryResponse?.recorded_at
      ? (llmPrimaryResponseFailed
        ? "Latest reply attempt failed"
        : (llmReplyLaneDriftCount > 0
          ? `${llmReplyLaneDriftCount} reply lane${llmReplyLaneDriftCount === 1 ? "" : "s"} differ from config`
          : `Last ${llmPrimaryResponse.lane_label || llmPrimaryResponse.activity_label || "reply"} ${formatRelative(Number(llmPrimaryResponse.recorded_at))}`))
      : (llmReplyLaneDriftCount > 0
        ? `${llmReplyLaneDriftCount} reply lane${llmReplyLaneDriftCount === 1 ? "" : "s"} differ from config`
        : `${Number(runtime?.llm?.routing_issue_count || llmRouteIssues.length)} route issue${Number(runtime?.llm?.routing_issue_count || llmRouteIssues.length) === 1 ? "" : "s"}`));
  const voiceControlCardSummary = voiceMissionControlSummary || (runtime?.voice?.bootstrap_ok ? "Ready" : "Needs attention");
  const voiceControlCardDetail = voiceMissionControlDetail
    || (`Default ${runtime?.voice?.voice_engine_default_label || runtime?.voice?.voice_engine_default || "classic"}`
      + (nativeBrowserVoicePath
        ? ` | Browser native ${nativeBrowserVoicePath.effective_engine_label || nativeBrowserVoicePath.effective_engine || "classic"}`
        : ""));
  const voiceControlCardAttention = voiceMissionControlAttention
    || (browserVoiceAttentionCount > 0
      ? `${browserVoiceAttentionCount} browser voice path${browserVoiceAttentionCount === 1 ? "" : "s"} need attention`
      : (directBrowserVoicePath
        ? `Direct Realtime ${formatRuntimeStatusLabel(String(directBrowserVoicePath.status || "idle"))}`
        : "Browser voice status not reported"));
  const topWarnings = useMemo(() => (mission?.warnings || []).slice(0, 4), [mission]);

  const runAction = useCallback(
    async <T,>(label: string, action: () => Promise<T>, onSuccess?: (result: T) => void | Promise<void>) => {
      setBusyAction(label);
      try {
        const result = await action();
        await onSuccess?.(result);
      } catch (e) {
        devLogError(`Mission control action failed: ${label}`, e);
        toast({
          title: "Mission Control",
          message: e instanceof Error ? e.message : `Failed to run ${label}`,
          variant: "error",
        });
      } finally {
        setBusyAction((current) => (current === label ? null : current));
      }
    },
    [toast],
  );

  const handleToggleThreadHistory = useCallback(
    (threadId: string) => {
      const cleanThreadId = String(threadId || "").trim();
      if (!cleanThreadId) return;
      if (expandedThreadHistoryId === cleanThreadId) {
        setExpandedThreadHistoryId(null);
        return;
      }

      const existingLimit = Number(threadHistoryById[cleanThreadId]?.history?.limit || 0);
      void loadThreadHistory(cleanThreadId, existingLimit || THREAD_HISTORY_BATCH_SIZE);
    },
    [expandedThreadHistoryId, loadThreadHistory, threadHistoryById],
  );

  const handleLoadMoreThreadHistory = useCallback(
    (threadId: string) => {
      const cleanThreadId = String(threadId || "").trim();
      if (!cleanThreadId) return;
      const currentLimit = Number(threadHistoryById[cleanThreadId]?.history?.limit || THREAD_HISTORY_BATCH_SIZE);
      const nextLimit = Math.min(currentLimit + THREAD_HISTORY_BATCH_SIZE, THREAD_HISTORY_MAX_LIMIT);
      void loadThreadHistory(cleanThreadId, nextLimit);
    },
    [loadThreadHistory, threadHistoryById],
  );

  const handleReminderCheckNow = useCallback(() => {
    void runAction("reminders-check-now", () => apiPost("/api/web/runtime/reminders/check-now", {}), (section: any) => {
      setMission((current) =>
        current
          ? { ...current, sections: { ...current.sections, reminders: section }, generated_at: Date.now() / 1000 }
          : current,
      );
    });
  }, [runAction]);

  const handleReminderCancel = useCallback(
    (reminderId: number) => {
      void runAction(`reminder-cancel:${reminderId}`, () => apiPost(`/api/web/runtime/reminders/${reminderId}/cancel`, {}), (section: any) => {
        setMission((current) =>
          current
            ? { ...current, sections: { ...current.sections, reminders: section }, generated_at: Date.now() / 1000 }
            : current,
        );
      });
    },
    [runAction],
  );

  const handleReminderSnooze = useCallback(
    (reminderId: number) => {
      void runAction(
        `reminder-snooze:${reminderId}`,
        () => apiPost(`/api/web/runtime/reminders/${reminderId}/snooze`, { minutes: 15 }),
        (payload: any) => {
          if (!payload?.section) return;
          setMission((current) =>
            current
              ? { ...current, sections: { ...current.sections, reminders: payload.section }, generated_at: Date.now() / 1000 }
              : current,
          );
        },
      );
    },
    [runAction],
  );

  const handleHeartbeatAction = useCallback(
    (action: "check-now" | "pause" | "resume") => {
      void runAction(
        `heartbeat-${action}`,
        () => apiPost(`/api/web/runtime/heartbeat/${action}`, {}),
        (section: any) => {
          setMission((current) =>
            current
              ? {
                  ...current,
                  sections: { ...current.sections, heartbeat: section, proactive: section },
                  generated_at: Date.now() / 1000,
                }
              : current,
          );
        },
      );
    },
    [runAction],
  );

  const handleStandingOrderAction = useCallback(
    (standingOrderId: string, action: "pause" | "resume" | "disable" | "run-now") => {
      void runAction(
        `standing-order:${action}:${standingOrderId}`,
        () => apiPost(`/api/web/runtime/standing-orders/${encodeURIComponent(standingOrderId)}/${action}`, {}),
        (section: any) => {
          setMission((current) =>
            current
              ? {
                  ...current,
                  sections: { ...current.sections, standing_orders: section },
                  generated_at: Date.now() / 1000,
                }
              : current,
          );
        },
      );
    },
    [runAction],
  );

  const setVoiceDraftField = useCallback((sessionKey: string, field: keyof VoiceDraft, value: string) => {
    setVoiceDrafts((current) => ({
      ...current,
      [sessionKey]: {
        ...(current[sessionKey] || DEFAULT_VOICE_DRAFT),
        [field]: value,
      },
    }));
  }, []);

  const handleVoiceApply = useCallback(
    (sessionKey: string) => {
      const draft = voiceDrafts[sessionKey];
      if (!draft) return;
      void runAction(
        `voice-apply:${sessionKey}`,
        () => apiPatch(`/api/web/runtime/voice/sessions/${encodeURIComponent(sessionKey)}`, draft),
        (payload: any) => {
          if (!payload?.voice) return;
          syncVoiceDrafts(payload.voice);
          setMission((current) =>
            current
              ? { ...current, sections: { ...current.sections, voice: payload.voice }, generated_at: Date.now() / 1000 }
              : current,
          );
        },
      );
    },
    [runAction, syncVoiceDrafts, voiceDrafts],
  );

  const handleLocalVoiceRuntimeApply = useCallback(() => {
    const draft = voiceDrafts[LOCAL_VOICE_RUNTIME_DRAFT_KEY];
    if (!draft) return;
    void runAction(
      "voice-local-apply",
      () => apiPatch("/api/web/runtime/voice/local", draft),
      (payload: any) => {
        if (!payload?.voice) return;
        syncVoiceDrafts(payload.voice);
        setMission((current) =>
          current
            ? { ...current, sections: { ...current.sections, voice: payload.voice }, generated_at: Date.now() / 1000 }
            : current,
        );
      },
    );
  }, [runAction, syncVoiceDrafts, voiceDrafts]);

  const setLocalVoiceDraftField = useCallback(
    (field: keyof VoiceDraft, value: string) => {
      setVoiceDraftField(LOCAL_VOICE_RUNTIME_DRAFT_KEY, field, value);
    },
    [setVoiceDraftField],
  );

  const handleVoiceSessionAction = useCallback(
    (sessionKey: string, action: "abort" | "stop") => {
      void runAction(
        `voice-${action}:${sessionKey}`,
        () => apiPost(`/api/web/runtime/voice/sessions/${encodeURIComponent(sessionKey)}/${action}`, {}),
        (payload: any) => {
          if (!payload?.voice) return;
          syncVoiceDrafts(payload.voice);
          setMission((current) =>
            current
              ? { ...current, sections: { ...current.sections, voice: payload.voice }, generated_at: Date.now() / 1000 }
              : current,
          );
        },
      );
    },
    [runAction, syncVoiceDrafts],
  );

  const handleJobAction = useCallback(
    (jobId: string, action: "cancel" | "snooze" | "requeue") => {
      const payload = action === "snooze" ? { minutes: 15 } : {};
      void runAction(`job-${action}:${jobId}`, () => apiPost(`/api/web/runtime/jobs/${jobId}/${action}`, payload), async () => {
        await fetchMission({ silent: true });
      });
    },
    [fetchMission, runAction],
  );

  const handleContextSimulation = useCallback(
    (eventType: string) => {
      void runAction(
        `context-simulate:${eventType}`,
        () => apiPost("/api/web/runtime/context/simulate", { event_type: eventType }),
        (payload: any) => {
          setMission((current) =>
            current
              ? {
                  ...current,
                  sections: {
                    ...current.sections,
                    ...(payload?.context ? { context: payload.context } : {}),
                    ...(payload?.ambient ? { ambient: payload.ambient } : {}),
                  },
                  generated_at: Date.now() / 1000,
                }
              : current,
          );
        },
      );
    },
    [runAction],
  );

  const handleOperatorWorkspace = useCallback(
    (
      mode: "active" | "pinned",
      action: "set" | "clear",
      payload?: { workspace_root: string; workspace_label?: string; remote_name?: string },
    ) => {
      const label = `operator-workspace:${mode}:${action}:${payload?.workspace_root || "none"}`;
      void runAction(
        label,
        () => {
          if (action === "clear") {
            return apiDelete(`/api/web/runtime/operator/workspace/${mode}`);
          }
          return apiPost(`/api/web/runtime/operator/workspace/${mode}`, payload || {});
        },
        (response: any) => {
          if (!response?.section) return;
          setMission((current) =>
            current
              ? { ...current, sections: { ...current.sections, operator: response.section }, generated_at: Date.now() / 1000 }
              : current,
          );
        },
      );
    },
    [runAction],
  );

  const handleThreadAction = useCallback(
    (threadId: string, action: "reset" | "archive") => {
      void runAction(
        `thread:${action}:${threadId}`,
        () => apiPost(`/api/web/runtime/threads/${encodeURIComponent(threadId)}/${action}`, {}),
        (response: any) => {
          if (!response?.section) return;
          setExpandedThreadHistoryId((current) => (current === threadId ? null : current));
          setThreadHistoryById((current) => {
            const next = { ...current };
            delete next[threadId];
            return next;
          });
          setThreadHistoryErrors((current) => {
            const next = { ...current };
            delete next[threadId];
            return next;
          });
          setMission((current) =>
            current
              ? { ...current, sections: { ...current.sections, sessions: response.section }, generated_at: Date.now() / 1000 }
              : current,
          );
        },
      );
    },
    [runAction],
  );

  const handleOperatorAction = useCallback(
    (actionId: string, action: "cancel" | "retry") => {
      void runAction(
        `operator-action:${action}:${actionId}`,
        () => apiPost(`/api/web/runtime/operator/actions/${actionId}/${action}`, {}),
        (response: any) => {
          if (!response?.section) return;
          setMission((current) =>
            current
              ? { ...current, sections: { ...current.sections, operator: response.section }, generated_at: Date.now() / 1000 }
              : current,
          );
        },
      );
    },
    [runAction],
  );

  const handleOperatorWorkflow = useCallback(
    (workflowId: string, action: "resume" | "pause" | "cancel" | "retry-step" | "new-attempt") => {
      void runAction(
        `operator-workflow:${action}:${workflowId}`,
        () => apiPost(`/api/web/runtime/operator/workflows/${workflowId}/${action}`, {}),
        (response: any) => {
          if (!response?.section) return;
          setMission((current) =>
            current
              ? { ...current, sections: { ...current.sections, operator: response.section }, generated_at: Date.now() / 1000 }
              : current,
          );
        },
      );
    },
    [runAction],
  );

  const handleOperatorWorkflowAnswer = useCallback(
    (workflowId: string, payload: { answer_text?: string; selected_choice?: string }) => {
      void runAction(
        `operator-workflow:answer:${workflowId}`,
        () =>
          apiPost(`/api/web/runtime/operator/workflows/${workflowId}/answer`, {
            answer_text: payload.answer_text || "",
            selected_choice: payload.selected_choice || undefined,
            source: "dashboard",
          }),
        (response: any) => {
          if (!response?.section) return;
          setMission((current) =>
            current
              ? { ...current, sections: { ...current.sections, operator: response.section }, generated_at: Date.now() / 1000 }
              : current,
          );
          setWorkflowAnswerDrafts((current) => ({ ...current, [workflowId]: "" }));
        },
      );
    },
    [runAction],
  );

  const handleOperatorFollowthrough = useCallback(
    (followthroughId: string, action: "snooze" | "dismiss" | "resolve", minutes?: number) => {
      void runAction(
        `operator-followthrough:${action}:${followthroughId}`,
        () =>
          apiPost(`/api/web/runtime/operator/followthrough/${followthroughId}/${action}`, {
            ...(action === "snooze" && typeof minutes === "number" ? { minutes } : {}),
          }),
        (response: any) => {
          if (!response?.section) return;
          setMission((current) =>
            current
              ? { ...current, sections: { ...current.sections, operator: response.section }, generated_at: Date.now() / 1000 }
              : current,
          );
        },
      );
    },
    [runAction],
  );

  const handleOperatorBrowser = useCallback(
    (action: "focus" | "reset") => {
      void runAction(
        `operator-browser:${action}`,
        () => apiPost(`/api/web/runtime/operator/browser/${action}`, {}),
        (response: any) => {
          if (!response?.section) return;
          setMission((current) =>
            current
              ? { ...current, sections: { ...current.sections, operator: response.section }, generated_at: Date.now() / 1000 }
              : current,
          );
        },
      );
    },
    [runAction],
  );

  const handleOperatorDesktopInspect = useCallback(() => {
    void runAction(
      "operator-desktop:inspect",
      () => apiPost("/api/web/runtime/operator/desktop/inspect", { capture_scope: "focused_window" }),
      (response: any) => {
        if (!response?.section) return;
        setMission((current) =>
          current
            ? { ...current, sections: { ...current.sections, operator: response.section }, generated_at: Date.now() / 1000 }
            : current,
        );
      },
    );
  }, [runAction]);

  const handleOperatorAppFocus = useCallback(
    (appId: string) => {
      void runAction(
        `operator-app:focus:${appId}`,
        () => apiPost(`/api/web/runtime/operator/apps/${appId}/focus`, {}),
        (response: any) => {
          if (!response?.section) return;
          setMission((current) =>
            current
              ? { ...current, sections: { ...current.sections, operator: response.section }, generated_at: Date.now() / 1000 }
              : current,
          );
        },
      );
    },
    [runAction],
  );

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center rounded-2xl border border-white/5 bg-black/40 text-white/20 backdrop-blur-md">
        <Loader2 className="animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-2xl border border-white/5 bg-black/40 shadow-xl backdrop-blur-md">
      <div className="flex h-12 items-center justify-between border-b border-white/5 bg-white/[0.02] px-4">
        <div className="flex items-center gap-2 text-xs font-mono uppercase tracking-widest text-white/40">
          <TerminalSquare className="h-3.5 w-3.5" />
          <span>Owner Mission Control</span>
        </div>
        <button
          type="button"
          onClick={() => fetchMission({ silent: true })}
          className="rounded-lg border border-white/10 bg-white/5 p-1.5 text-white/60 transition-colors hover:bg-white/10 disabled:opacity-50"
          disabled={refreshing}
          aria-label="Refresh mission control"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
        </button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4 scrollbar-thin scrollbar-thumb-white/10">
        {error ? <div className="text-sm text-red-300/80">{error}</div> : null}

        {mission && runtime ? (
          <>
            {topWarnings.length > 0 ? (
              <div className="space-y-2">
                {topWarnings.map((warning) => {
                  const warningActions = buildMissionWarningActions(warning);
                  return (
                    <div
                      key={warning.code}
                      className={`rounded-xl border px-3 py-2 text-xs ${warningTone(warning.severity)}`}
                    >
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                        <div className="flex items-start gap-2">
                          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                          <span>{warning.message}</span>
                        </div>
                        {warningActions.length ? (
                          <div className="flex flex-wrap gap-2 sm:justify-end">
                            {warningActions.map((action) => (
                              <Link
                                key={`${warning.code}:${action.href}`}
                                href={action.href}
                                className="rounded-lg border border-current/20 bg-black/10 px-2 py-1 text-[11px] font-medium transition-colors hover:bg-black/20"
                              >
                                {action.label}
                              </Link>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : null}

            <SectionCard
              icon={<Shield className="h-4 w-4" />}
              title="Runtime Health"
              status={mission.sections.health.status}
              anchorId="runtime-health"
            >
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Mode</div>
                  <div className="mt-1 text-white/90">{runtime.deployment.mode}</div>
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Operator</div>
                  <div className="mt-1 text-white/90">{runtime.deployment.owner_display_name || runtime.operator.email}</div>
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Reply Brain</div>
                  <div className="mt-1 text-white/90">{replyBrainCardSummary}</div>
                  {replyBrainCardDetail ? <div className="mt-1 truncate text-white/45">{replyBrainCardDetail}</div> : null}
                  {replyBrainCardAttention ? (
                    <div className={`mt-1 truncate ${llmMissionControlAttention || llmPrimaryResponseFailed || llmReplyLaneDriftCount > 0 ? "text-amber-200" : "text-white/35"}`}>
                      {replyBrainCardAttention}
                    </div>
                  ) : null}
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Voice Control</div>
                  <div className="mt-1 text-white/90">{voiceControlCardSummary}</div>
                  {voiceControlCardDetail ? <div className="mt-1 truncate text-white/45">{voiceControlCardDetail}</div> : null}
                  {voiceControlCardAttention ? (
                    <div className={`mt-1 truncate ${voiceMissionControlAttention || browserVoiceAttentionCount > 0 ? "text-amber-200" : "text-white/35"}`}>
                      {voiceControlCardAttention}
                    </div>
                  ) : null}
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Backend</div>
                  <div className="mt-1 text-white/90">{backendCardSummary}</div>
                  {backendCardDetail ? (
                    <div
                      className={`mt-1 truncate ${
                        String(health?.backend?.status || "").trim().toLowerCase() === "degraded" || backendStartupSummary
                          ? "text-amber-200"
                          : "text-white/45"
                      }`}
                    >
                      {backendCardDetail}
                    </div>
                  ) : null}
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Frontend</div>
                  <div className="mt-1 text-white/90">{frontendCardSummary}</div>
                  {frontendCardDetail ? (
                    <div
                      className={`mt-1 truncate ${
                        String(health?.frontend?.status || "").trim().toLowerCase() === "degraded"
                          ? "text-amber-200"
                          : "text-white/45"
                      }`}
                    >
                      {frontendCardDetail}
                    </div>
                  ) : null}
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Doctor</div>
                  <div className="mt-1 text-white/90">{doctorCardSummary}</div>
                  <div
                    className={`mt-1 ${
                      Number(health?.doctor?.counts?.core_blockers || 0) > 0 || Number(health?.doctor?.counts?.warnings || 0) > 0
                        ? "text-amber-200"
                        : "text-white/45"
                    }`}
                  >
                    {doctorCardDetail}
                  </div>
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Database</div>
                  <div className="mt-1 text-white/90">{databaseCardSummary}</div>
                  {databaseCardDetail ? (
                    <div
                      className={`mt-1 truncate ${
                        health?.database?.writable
                        && !String(health?.database?.mission_control_detail || "").includes("DELETE journal mode")
                          ? "text-white/45"
                          : "text-amber-200"
                      }`}
                    >
                      {databaseCardDetail}
                    </div>
                  ) : null}
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Host</div>
                  <div className="mt-1 text-white/90">
                    {String(health?.host?.description || health?.host?.host_backend || "unknown")}
                  </div>
                  <div className="mt-1 text-white/45">
                    {String(health?.host?.host_platform || "unknown")}
                    {health?.host?.display_server ? ` • ${String(health.host.display_server)}` : ""}
                  </div>
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Plugins</div>
                  <div className="mt-1 text-white/90">{String(health?.plugins?.status || plugins?.status || "unknown")}</div>
                  <div className="mt-1 text-white/45">
                    Enabled {Number(health?.plugins?.counts?.enabled || plugins?.counts?.enabled || 0)} / {Number(health?.plugins?.counts?.total || plugins?.counts?.total || 0)}
                    {Boolean(health?.plugins?.restart_pending ?? plugins?.restart_pending) ? " • restart pending" : ""}
                  </div>
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Channels</div>
                  <div className="mt-1 text-white/90">
                    {Number(channels?.counts?.connected_integrations || health?.channels?.runtime?.connected_platforms?.length || 0)} connected
                  </div>
                  <div className="mt-1 text-white/45">
                    Linked {Number(channels?.counts?.linked_dms || 0)} • Spaces {Number(channels?.counts?.mapped_spaces || 0)}
                  </div>
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Nodes</div>
                  <div className="mt-1 text-white/90">{String(health?.nodes?.status || nodes?.status || "unknown")}</div>
                  <div className="mt-1 text-white/45">
                    Connected {Number(health?.nodes?.counts?.connected || nodes?.counts?.connected || 0)} / {Number(health?.nodes?.counts?.total || nodes?.counts?.total || 0)}
                  </div>
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="text-white/40">Surface Reliability</div>
                  <div className="mt-1 text-white/90">
                    {surfaceReliabilitySummary || "No recent machine-control reliability issues recorded."}
                  </div>
                  <div className={`mt-1 ${surfaceReliabilityAttention ? "text-amber-200" : "text-white/45"}`}>
                    {surfaceReliabilityAttention || surfaceReliabilityCountsLine}
                  </div>
                  {surfaceReliabilityDetailLine ? (
                    <div className="mt-1 text-white/35">
                      {surfaceReliabilityDetailLine}
                    </div>
                  ) : null}
                </div>
              </div>
              {health?.doctor?.summary ? (
                <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2 text-xs text-white/70">
                  {String(health.doctor.summary)}
                </div>
              ) : null}
              <div className="mt-3 grid gap-3 xl:grid-cols-2">
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-white/40">LLM Routing</div>
                    <div className={statusTone(runtime.llm.routing_ready ? "ready" : "degraded")}>
                      {runtime.llm.routing_ready ? "ready" : "needs attention"}
                    </div>
                  </div>
                  <div className="mt-2 text-white/80">
                    Response route: {formatLlmRouteReference(llmResponseRoute)}
                    {llmResponseRoute?.route_changed ? ` • requested ${formatLlmRouteReference(llmResponseRoute, "requested")}` : ""}
                    {llmResponseRoute?.model ? ` • ${llmResponseRoute.model}` : ""}
                  </div>
                  <div className="mt-1 text-white/50">
                    Route issues {Number(runtime.llm.routing_issue_count || llmRouteIssues.length)} • Critical {Number(runtime.llm.critical_route_failure_count || 0)}
                  </div>
                  <div className="mt-3 rounded-xl border border-white/5 bg-black/20 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-white/75">Recent actual usage</span>
                      <span className="text-white/45">{llmRecentActivity?.scope || "none"}</span>
                    </div>
                    <div className="mt-1 text-white/45">
                      Calls {Number(llmRecentActivity?.call_count || 0)} • Success {Number(llmRecentActivity?.success_count || 0)} • Failures {Number(llmRecentActivity?.failure_count || 0)}
                    </div>
                    {llmLastObservedResponse ? (
                      <div className={`mt-2 ${llmLastObservedResponse.success === false ? "text-amber-100/80" : "text-white/80"}`}>
                        {llmLastObservedResponse.success === false ? "Latest reply attempt failed" : "Last reply"}: {formatLlmActivity(llmLastObservedResponse)}
                        {llmLastObservedResponse.recorded_at ? ` • ${formatRelative(Number(llmLastObservedResponse.recorded_at))}` : ""}
                        {llmLastObservedResponse.success === false && llmLastObservedResponse.error ? ` • ${llmLastObservedResponse.error}` : ""}
                      </div>
                    ) : (
                      <div className="mt-2 text-white/55">
                        No reply call has been observed in this process yet.
                      </div>
                    )}
                    {llmLastActualResponse && !sameLlmActivity(llmLastActualResponse, llmLastObservedResponse) ? (
                      <div className="mt-1 text-white/50">
                        Last successful reply: {formatLlmActivity(llmLastActualResponse)}
                        {llmLastActualResponse.recorded_at ? ` • ${formatRelative(Number(llmLastActualResponse.recorded_at))}` : ""}
                      </div>
                    ) : null}
                    {llmLastFailedResponse && !sameLlmActivity(llmLastFailedResponse, llmLastObservedResponse) ? (
                      <div className="mt-1 text-amber-200/80">
                        Last failed reply: {formatLlmActivity(llmLastFailedResponse)}
                        {llmLastFailedResponse.recorded_at ? ` • ${formatRelative(Number(llmLastFailedResponse.recorded_at))}` : ""}
                        {llmLastFailedResponse.error ? ` • ${llmLastFailedResponse.error}` : ""}
                      </div>
                    ) : null}
                    {llmGlobalFailedResponse && !sameLlmActivity(llmGlobalFailedResponse, llmLastFailedResponse) ? (
                      <div className="mt-1 text-amber-100/70">
                        Background/global failure: {formatLlmActivity(llmGlobalFailedResponse)}
                        {llmGlobalFailedResponse.recorded_at ? ` • ${formatRelative(Number(llmGlobalFailedResponse.recorded_at))}` : ""}
                        {llmGlobalFailedResponse.error ? ` • ${llmGlobalFailedResponse.error}` : ""}
                      </div>
                    ) : null}
                    {llmLastObservedCall && !sameLlmActivity(llmLastObservedCall, llmLastObservedResponse) ? (
                      <div className="mt-1 text-white/50">
                        Last LLM call: {formatLlmActivity(llmLastObservedCall)}
                        {llmLastObservedCall.recorded_at ? ` • ${formatRelative(Number(llmLastObservedCall.recorded_at))}` : ""}
                        {llmLastObservedCall.success === false && llmLastObservedCall.error ? ` • ${llmLastObservedCall.error}` : ""}
                      </div>
                    ) : null}
                    {llmResponseLanes.length ? (
                      <div className="mt-3 grid gap-2 md:grid-cols-2">
                        {llmResponseLanes.map((lane) => {
                          const laneStatus = llmLaneStatus(lane);
                          const lastResponse = lane.last_response;
                          const lastSuccess = lane.last_successful_response;
                          const lastFailure = lane.last_failed_response;
                          return (
                            <div key={lane.lane} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-white/80">{lane.lane_label || lane.lane}</span>
                                <span className={statusTone(laneStatus)}>{laneStatus === "ready" ? "ok" : laneStatus}</span>
                              </div>
                              <div className="mt-1 text-white/45">
                                Calls {Number(lane.call_count || 0)} • Success {Number(lane.success_count || 0)} • Failures {Number(lane.failure_count || 0)}
                              </div>
                              {lastResponse ? (
                                <div className={`mt-1 ${lastResponse.success === false ? "text-amber-100/80" : "text-white/70"}`}>
                                  Last: {formatLlmProviderModel(lastResponse)}
                                  {lastResponse.recorded_at ? ` • ${formatRelative(Number(lastResponse.recorded_at))}` : ""}
                                  {lastResponse.success === false && lastResponse.error ? ` • ${lastResponse.error}` : ""}
                                </div>
                              ) : (
                                <div className="mt-1 text-white/45">No recent reply activity.</div>
                              )}
                              {lastSuccess && !sameLlmActivity(lastSuccess, lastResponse) ? (
                                <div className="mt-1 text-white/45">
                                  Last success: {formatLlmProviderModel(lastSuccess)}
                                  {lastSuccess.recorded_at ? ` • ${formatRelative(Number(lastSuccess.recorded_at))}` : ""}
                                </div>
                              ) : null}
                              {lastFailure && !sameLlmActivity(lastFailure, lastResponse) ? (
                                <div className="mt-1 text-amber-200/75">
                                  Last failure: {formatLlmProviderModel(lastFailure)}
                                  {lastFailure.recorded_at ? ` • ${formatRelative(Number(lastFailure.recorded_at))}` : ""}
                                  {lastFailure.error ? ` • ${lastFailure.error}` : ""}
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    ) : null}
                    {llmRecentActivity?.recent_calls?.length ? (
                      <div className="mt-3 space-y-2">
                        {llmRecentActivity.recent_calls.slice(0, 3).map((call, index) => (
                          <div key={`${call.log_context || call.activity_label || "call"}-${index}`} className="flex items-center justify-between gap-3 text-[11px] text-white/55">
                            <span className="truncate">
                              {call.activity_label || call.log_context || "Unknown"}
                              {call.provider_label || call.provider ? ` • ${call.provider_label || call.provider}` : ""}
                            </span>
                            <span className={call.success ? "text-emerald-300" : "text-amber-200"}>
                              {call.recorded_at ? formatRelative(Number(call.recorded_at)) : call.success ? "ok" : "failed"}
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  {llmReplySurfaces.length ? (
                    <div className="mt-3 grid gap-2">
                      {llmReplySurfaces.map((surface) => {
                        const attentionSummary = describeReplySurfaceAttention(surface);
                        const controlLinks = replySurfaceControlLinks(surface);
                        const laneSummary = llmResponseLaneById[surface.id];
                        const laneLastResponse = laneSummary?.last_response || laneSummary?.last_successful_response || null;
                        const surfaceStatus =
                          laneLastResponse?.success === false || attentionSummary
                            ? "degraded"
                            : String(surface.status || "ready");
                        return (
                          <ControlSurfaceCard
                            key={surface.id}
                            title={surface.label}
                            status={surfaceStatus}
                            summary={
                              surface.routing_kind === "direct_openai_realtime"
                                ? "Direct browser lane"
                                : `PaLLi ${surface.family} lane`
                            }
                            configuredValue={formatLlmRouteSummary(surface, "requested")}
                            runningValue={
                              laneLastResponse
                                ? formatLlmProviderModel(laneLastResponse)
                                : formatLlmRouteSummary(surface)
                            }
                            attention={attentionSummary || undefined}
                            actions={controlLinks}
                            details={
                              <>
                                {laneSummary ? (
                                  <div>
                                    Recent reply:{" "}
                                    {laneLastResponse
                                      ? `${formatLlmProviderModel(laneLastResponse)}${laneLastResponse.recorded_at ? ` | ${formatRelative(Number(laneLastResponse.recorded_at))}` : ""}${laneLastResponse.success === false && laneLastResponse.error ? ` | ${laneLastResponse.error}` : ""}`
                                      : `No recent reply activity. Calls ${Number(laneSummary.call_count || 0)} | Success ${Number(laneSummary.success_count || 0)} | Failures ${Number(laneSummary.failure_count || 0)}`}
                                  </div>
                                ) : null}
                                {surface.engine ? (
                                  <div>
                                    Speech engine: {formatVoiceEngineSummary(surface.engine, "effective")}
                                    {formatVoiceEngineSummary(surface.engine, "requested") !== formatVoiceEngineSummary(surface.engine, "effective")
                                      ? ` | requested ${formatVoiceEngineSummary(surface.engine, "requested")}`
                                      : ""}
                                  </div>
                                ) : null}
                                {surface.voice || surface.transcription_model ? (
                                  <div>
                                    {surface.voice ? `Voice ${surface.voice}` : ""}
                                    {surface.voice && surface.transcription_model ? " | " : ""}
                                    {surface.transcription_model ? `Transcription ${surface.transcription_model}` : ""}
                                  </div>
                                ) : null}
                                {surface.requested_provider_source || surface.requested_model_source ? (
                                  <div>
                                    Source {surface.requested_provider_source ? formatRouteSource(surface.requested_provider_source) : "configured route"}
                                    {surface.requested_model_source ? ` | ${formatRouteSource(surface.requested_model_source)}` : ""}
                                  </div>
                                ) : null}
                                {surface.issue?.message && !attentionSummary ? <div className="text-amber-100/70">{surface.issue.message}</div> : null}
                                {surface.note ? <div>{surface.note}</div> : null}
                              </>
                            }
                          />
                        );
                      })}
                    </div>
                  ) : null}
                  <div className="mt-3 space-y-2">
                    {llmRouteExceptionEntries.length ? (
                      llmRouteExceptionEntries.map(([task, route]) => {
                        const severity = String(route.issue?.severity || "").trim().toLowerCase();
                        const attention =
                          route.issue?.message
                          || (route.route_changed
                            ? `Configured for ${formatLlmRouteSummary(route, "requested")}; runtime resolves to ${formatLlmRouteSummary(route)}.`
                            : "");
                        return (
                          <div key={task} className="rounded-xl border border-white/5 bg-black/20 px-3 py-2">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-white/80">{formatReason(task)}</span>
                              <span className={statusTone(severity || (route.route_changed ? "degraded" : "ready"))}>
                                {formatRuntimeStatusLabel(severity || (route.route_changed ? "degraded" : "ready"))}
                              </span>
                            </div>
                            <div className="mt-1 text-white/55">
                              Requested {formatLlmRouteReference(route, "requested")}
                              {route.model ? ` • ${route.model}` : ""}
                            </div>
                            <div className="mt-1 text-white/70">Effective {formatLlmRouteReference(route)}</div>
                            <div className="mt-1 text-white/45">
                              {route.requested_provider_source ? formatRouteSource(route.requested_provider_source) : "configured route"}
                              {route.requested_model_source ? ` | ${formatRouteSource(route.requested_model_source)}` : ""}
                            </div>
                            {attention ? (
                              <div className="mt-1 text-amber-100/70">{attention}</div>
                            ) : null}
                          </div>
                        );
                      })
                    ) : (
                      <div className="rounded-xl border border-white/5 bg-black/20 px-3 py-2 text-white/55">
                        Background task routes match the configured providers and models.
                      </div>
                    )}
                  </div>
                </div>
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-white/40">Browser Voice Paths</div>
                    <div className={statusTone(String(browserVoiceActivity?.status || "idle"))}>
                      {browserVoiceActivity?.current_path_label || "idle"}
                    </div>
                  </div>
                  {browserVoiceActivity?.summary ? (
                    <div className="mt-2 text-white/55">{browserVoiceActivity.summary}</div>
                  ) : (
                    <div className="mt-2 text-white/55">Native PaLLi stays primary.</div>
                  )}
                  <div className="mt-3 space-y-2">
                    {browserVoicePaths.map((path) => {
                      const liveActivity = browserVoiceActivityById[path.id];
                      const liveState = String(liveActivity?.activity_state || "").trim().toLowerCase();
                      const visibleStatus = liveState || String(path.status || "unknown");
                      const attentionSummary = describeBrowserVoicePathAttention(path);
                      const controlLinks = browserVoicePathControlLinks(path);
                      const configuredValue =
                        path.id === "native_palli"
                          ? path.requested_engine_label || path.requested_engine || "classic"
                          : formatModelVoiceSummary(path.model, path.voice);
                      const runningValue =
                        path.id === "native_palli"
                          ? path.effective_engine_label || path.effective_engine || "classic"
                          : liveActivity
                            ? formatModelVoiceSummary(liveActivity.model, liveActivity.voice, path.available === false ? "Unavailable" : "Idle")
                            : path.available === false
                              ? "Unavailable"
                              : formatModelVoiceSummary(path.model, path.voice, "Idle");
                      return (
                        <ControlSurfaceCard
                          key={path.id}
                          title={path.label}
                          status={visibleStatus}
                          summary={path.note}
                          configuredValue={configuredValue}
                          runningValue={runningValue}
                          attention={attentionSummary || undefined}
                          actions={controlLinks}
                          details={
                            <>
                              {liveActivity ? (
                                <div>
                                  Sessions {Number(liveActivity.active_sessions || 0)} active | Recent {Number(liveActivity.recent_sessions || 0)}
                                  {liveActivity.last_activity_at ? ` | ${formatRelative(Number(liveActivity.last_activity_at))}` : ""}
                                </div>
                              ) : null}
                              {liveActivity?.last_activity_detail ? <div>{liveActivity.last_activity_detail}</div> : null}
                              {liveActivity?.last_floor_state_label ? <div>Floor state | {liveActivity.last_floor_state_label}</div> : null}
                              {liveActivity?.last_mirrored_excerpt ? <div>Last mirrored turn | {liveActivity.last_mirrored_excerpt}</div> : null}
                            </>
                          }
                        />
                      );
                    })}
                  </div>
                  {browserVoiceActivity?.recent_sessions?.length ? (
                    <div className="mt-3 rounded-xl border border-white/5 bg-black/20 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-white/40">Recent Browser Sessions</div>
                        <div className="text-white/45">{browserVoiceActivity.recent_sessions.length}</div>
                      </div>
                      <div className="mt-2 space-y-2">
                        {browserVoiceActivity.recent_sessions.slice(0, 4).map((session) => {
                          const sessionState = String(session.activity_state || "unknown").trim().toLowerCase() || "unknown";
                          return (
                            <div key={session.id} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-white/80">{session.path_label}</span>
                                <span className={statusTone(sessionState)}>{sessionState}</span>
                              </div>
                              <div className="mt-1 text-white/60">
                                {session.summary || session.detail || "Recent browser session"}
                                {session.last_activity_at ? ` | ${formatRelative(Number(session.last_activity_at))}` : ""}
                              </div>
                              {session.detail && session.detail !== session.summary ? (
                                <div className="mt-1 text-white/45">{session.detail}</div>
                              ) : null}
                              {session.excerpt ? (
                                <div className="mt-1 text-white/40">
                                  {session.excerpt_label ? `${session.excerpt_label}: ` : ""}
                                  {session.excerpt}
                                </div>
                              ) : null}
                              {session.fallback_active ? (
                                <div className="mt-1 text-amber-100/70">
                                  Fallback: {session.fallback_reason_label || formatReason(session.fallback_reason)}
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                  <div className="mt-3">
                    <div className="mb-2 text-[11px] uppercase tracking-[0.18em] text-white/35">Per-surface engine routing</div>
                    <div className="grid gap-2">
                      {voiceSurfaceEngines.map((surface) => (
                        <ControlSurfaceCard
                          key={surface.platform}
                          title={surface.label}
                          status={surface.fallback_active ? "degraded" : "ready"}
                          summary={`Supports ${formatSupportedVoiceEngines(surface.supported_engines)}`}
                          configuredValue={surface.requested_engine_label || surface.requested_engine || "classic"}
                          runningValue={surface.effective_engine_label || surface.effective_engine || "classic"}
                          attention={describeVoiceSurfaceEngineAttention(surface) || undefined}
                          actions={voiceSurfaceEngineControlLinks(surface)}
                          details={
                            <div>
                              {surface.bridge_surface_enabled
                                ? "OpenAI Realtime bridge can run on this surface."
                                : "This surface stays on classic speech I/O only."}
                            </div>
                          }
                        />
                      ))}
                    </div>
                  </div>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-[10px] font-medium uppercase tracking-widest text-white/65">
                <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1">
                  Agentic {runtime.features.agentic_enabled ? `${runtime.features.agentic_mode}/${runtime.features.agentic_rollout_mode}` : "off"}
                </span>
                <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1">
                  Voice {runtime.features.voice_enabled ? `${runtime.voice.stt_provider}/${runtime.voice.tts_provider}` : "off"}
                </span>
                <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1">
                  Discord {runtime.operator.discord_user_id ? "linked" : "not linked"}
                </span>
                <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1">
                  Telegram {runtime.features.telegram_enabled ? "enabled" : "off"} • Slack {runtime.features.slack_enabled ? "enabled" : "off"}
                </span>
              </div>
            </SectionCard>

            {channels ? (
              <SectionCard
                icon={<Link2 className="h-4 w-4" />}
                title="Channels"
                status={channels.status}
                anchorId="channels"
              >
                <div className="mb-3 flex flex-col gap-3 rounded-xl border border-white/5 bg-black/20 px-3 py-3 text-xs sm:flex-row sm:items-start sm:justify-between">
                  <div className="max-w-2xl text-white/50">
                    Mission control mirrors current channel status here. Discord linking, pairing codes, and mapped-space maintenance stay in owner controls.
                  </div>
                  {showOwnerControls ? (
                    <Link
                      href={OWNER_CONTROLS_HREF}
                      className="inline-flex items-center rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-[11px] font-medium text-white/75 transition-colors hover:bg-white/10"
                    >
                      Manage channels
                    </Link>
                  ) : null}
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Connected</div>
                    <div className="mt-1 text-white/90">{Number(channels.counts?.connected_integrations || 0)}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Linked DMs</div>
                    <div className="mt-1 text-white/90">{Number(channels.counts?.linked_dms || 0)}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Mapped Spaces</div>
                    <div className="mt-1 text-white/90">{Number(channels.counts?.mapped_spaces || 0)}</div>
                  </div>
                </div>
                {channels.summary || channels.attention_summary ? (
                  <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] px-3 py-3 text-xs">
                    {channels.summary ? <div className="text-white/75">{String(channels.summary)}</div> : null}
                    {channels.attention_summary ? (
                      <div className="mt-1 text-amber-100/70">{String(channels.attention_summary)}</div>
                    ) : null}
                  </div>
                ) : null}
                {(channels.platform_summaries || []).length > 0 ? (
                  <div className="mt-3 space-y-2">
                    {(channels.platform_summaries || []).slice(0, 4).map((item) => (
                      <div key={String(item.platform || item.label || Math.random())} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <div className="truncate text-white/85">{String(item.label || item.platform || "Channel")}</div>
                          <StatusPill status={String(item.status || "unknown")} />
                        </div>
                        <div className="mt-1 text-white/45">{String(item.summary || "Channel runtime status.")}</div>
                        {item.detail ? <div className="mt-1 truncate text-white/35">{String(item.detail)}</div> : null}
                      </div>
                    ))}
                  </div>
                ) : null}
                {(channels.owner_link_summaries || []).length > 0 ? (
                  <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] p-3 text-xs">
                    <div className="text-white/40">Owner Links</div>
                    <div className="mt-2 space-y-1.5">
                      {(channels.owner_link_summaries || []).slice(0, 3).map((item) => (
                        <div key={String(`${item.kind || "link"}:${item.platform || "unknown"}:${item.label || Math.random()}`)} className="rounded-lg border border-white/5 bg-black/20 px-2 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-white/75">{String(item.label || "linked surface")}</span>
                            <span className="text-white/45">{String(item.platform || "unknown")}</span>
                          </div>
                          <div className="mt-1 text-white/45">{String(item.summary || "Owner link saved.")}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </SectionCard>
            ) : null}

            {nodes ? (
              <SectionCard icon={<Smartphone className="h-4 w-4" />} title="Nodes" status={nodes.status}>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Total</div>
                    <div className="mt-1 text-white/90">{Number(nodes.counts?.total || 0)}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Connected</div>
                    <div className="mt-1 text-white/90">{Number(nodes.counts?.connected || 0)}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Pending Pairings</div>
                    <div className="mt-1 text-white/90">{Number(nodes.counts?.pending_pairings || 0)}</div>
                  </div>
                </div>
                {nodes.summary || nodes.attention_summary || nodes.preferred_notification_summary ? (
                  <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] px-3 py-3 text-xs">
                    {nodes.summary ? <div className="text-white/75">{String(nodes.summary)}</div> : null}
                    {nodes.preferred_notification_summary ? (
                      <div className="mt-1 text-white/45">{String(nodes.preferred_notification_summary)}</div>
                    ) : null}
                    {nodes.attention_summary ? (
                      <div className="mt-1 text-amber-100/70">{String(nodes.attention_summary)}</div>
                    ) : null}
                  </div>
                ) : null}
                {(nodes.recovery_summaries || []).length > 0 ? (
                  <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] p-3 text-xs">
                    <div className="text-white/40">Recent Recoveries</div>
                    <div className="mt-2 space-y-1.5">
                      {(nodes.recovery_summaries || []).slice(0, 3).map((item) => (
                        <div key={String(item.invocation_id || Math.random())} className="rounded-lg border border-white/5 bg-black/20 px-2 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-white/75">{formatReason(String(item.capability || "node_action"))}</span>
                            <span className={statusTone(String(item.status || "unknown"))}>{String(item.status || "unknown")}</span>
                          </div>
                          <div className="mt-1 text-white/45">
                            {String(item.metadata?.recovery_result || item.error_text || "Recovered")}
                            {item.metadata?.fallback_target ? ` • fallback ${String(item.metadata.fallback_target)}` : ""}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                {(nodes.node_summaries || []).length > 0 ? (
                  <div className="mt-3 space-y-2">
                    {(nodes.node_summaries || []).slice(0, 4).map((item) => (
                      <div key={String(item.node_id || item.label || Math.random())} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <div className="truncate text-white/85">
                            {String(item.label || item.node_id || "Node")}
                            {item.preferred_for_notifications ? " • preferred" : ""}
                          </div>
                          <StatusPill status={String(item.status || "unknown")} />
                        </div>
                        <div className="mt-1 truncate text-white/45">
                          {String(item.platform || "unknown")}
                          {item.host_backend ? ` • ${String(item.host_backend)}` : ""}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}
              </SectionCard>
            ) : null}

            {sessions ? (
              <SectionCard icon={<TerminalSquare className="h-4 w-4" />} title="Sessions" status={sessions.status}>
                {sessions.summary || sessions.current_thread_summary || sessions.attention_summary ? (
                  <div className="mb-3 text-xs">
                    {sessions.summary ? <div className="text-white/75">{String(sessions.summary)}</div> : null}
                    {sessions.current_thread_summary ? (
                      <div className="mt-1 text-white/45">{String(sessions.current_thread_summary)}</div>
                    ) : null}
                    {sessions.attention_summary ? (
                      <div className="mt-1 text-amber-100/70">{String(sessions.attention_summary)}</div>
                    ) : null}
                  </div>
                ) : null}
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Total</div>
                    <div className="mt-1 text-white/90">{sessions.counts.total}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Active</div>
                    <div className="mt-1 text-white/90">{sessions.counts.active}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Threads</div>
                    <div className="mt-1 text-white/90">{Number(sessions.counts.threads || 0)}</div>
                  </div>
                </div>
                <div className="mt-3 space-y-2 text-xs">
                  {(sessions.items || []).slice(0, 4).map((thread) => {
                    const threadId = String(thread.thread_id || "");
                    const historyPreview = Array.isArray(thread.history_preview) ? thread.history_preview.slice(-3) : [];
                    const historyPreviewUnavailable = String(thread.history_preview_status || "") === "unavailable";
                    const historyDetail = threadId ? threadHistoryById[threadId] : null;
                    const historyExpanded = Boolean(threadId) && expandedThreadHistoryId === threadId;
                    const historyItems = Array.isArray(historyDetail?.history?.items) ? historyDetail.history.items : [];
                    const historyError = threadId ? String(threadHistoryErrors[threadId] || "").trim() : "";
                    const historyLoading = Boolean(threadId) && String(threadHistoryBusyKey || "").startsWith(`${threadId}:`);
                    const historyHasMore = Boolean(historyDetail?.history?.has_more);
                    const historyLimit = Number(historyDetail?.history?.limit || 0);
                    const historyLatestTimestamp = Number(historyDetail?.history?.latest_timestamp || 0);
                    return (
                      <div key={String(thread.thread_id || thread.label)} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-white/85">{String(thread.label || thread.thread_kind || "Thread")}</span>
                          <span className={statusTone(String(thread.status || "ready"))}>{String(thread.status || "ready")}</span>
                        </div>
                        <div className="mt-1 truncate text-white/45">{threadId}</div>
                        <div className="mt-2 text-[11px] text-white/45">
                          Sessions {Number((thread.surface_sessions || []).length)} • Roots {Number((thread.root_agent_sessions || []).length)}
                          {thread.workspace_root ? ` • ${String(thread.workspace_root)}` : ""}
                        </div>
                        {historyPreview.length ? (
                          <div className="mt-3 space-y-2 border-t border-white/5 pt-2">
                            {historyPreview.map((entry, index) => {
                              const roleLabel = String(entry?.role_label || entry?.role || "Message");
                              const excerpt = String(entry?.excerpt || entry?.content || "").trim();
                              const voiceSummary = formatSessionHistoryVoiceSummary(entry);
                              const rawTimestamp = Number(entry?.timestamp || 0);
                              const relativeTimestamp = Number.isFinite(rawTimestamp) && rawTimestamp > 0 ? formatRelative(rawTimestamp) : "";
                              return (
                                <div key={`${String(thread.thread_id || thread.label)}:${String(entry?.id || index)}`} className="rounded-lg border border-white/5 bg-black/10 px-2 py-2">
                                  <div className="flex items-center justify-between gap-2 text-[11px]">
                                    <span className="text-white/55">{roleLabel}</span>
                                    {relativeTimestamp ? <span className="text-white/35">{relativeTimestamp}</span> : null}
                                  </div>
                                  {excerpt ? <div className="mt-1 text-white/75">{excerpt}</div> : null}
                                  {voiceSummary ? <div className="mt-1 text-[11px] text-cyan-100/70">Voice decision | {voiceSummary}</div> : null}
                                </div>
                              );
                            })}
                          </div>
                        ) : null}
                        {historyPreviewUnavailable ? (
                          <div className="mt-3 text-[11px] text-amber-100/70">Recent history preview unavailable.</div>
                        ) : null}
                        {historyExpanded ? (
                          <div className="mt-3 rounded-lg border border-cyan-400/15 bg-black/20 px-3 py-3">
                            <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                              <div className="uppercase tracking-[0.18em] text-cyan-100/65">Thread History</div>
                              <div className="text-white/45">
                                {Number(historyDetail?.history?.count || historyItems.length)} message{Number(historyDetail?.history?.count || historyItems.length) === 1 ? "" : "s"}
                                {historyLatestTimestamp > 0 ? ` • Updated ${formatRelative(historyLatestTimestamp)}` : ""}
                              </div>
                            </div>
                            {historyLoading ? (
                              <div className="mt-3 text-white/55">Loading full thread history…</div>
                            ) : null}
                            {!historyLoading && historyError ? (
                              <div className="mt-3 rounded-lg border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-[11px] text-amber-100/80">
                                {historyError}
                              </div>
                            ) : null}
                            {!historyLoading && !historyError && historyItems.length ? (
                              <div className="mt-3 max-h-80 space-y-2 overflow-y-auto pr-1">
                                {historyItems.map((entry, index) => {
                                  const roleLabel = String(entry?.role_label || entry?.role || "Message");
                                  const voiceSummary = formatSessionHistoryVoiceSummary(entry);
                                  const rawTimestamp = Number(entry?.timestamp || 0);
                                  const relativeTimestamp = Number.isFinite(rawTimestamp) && rawTimestamp > 0 ? formatRelative(rawTimestamp) : "";
                                  const content = String(entry?.content || entry?.excerpt || "").trim();
                                  return (
                                    <div key={`${threadId}:detail:${String(entry?.id || index)}`} className="rounded-lg border border-white/5 bg-white/[0.03] px-3 py-2">
                                      <div className="flex items-center justify-between gap-2 text-[11px]">
                                        <span className="text-white/55">{roleLabel}</span>
                                        {relativeTimestamp ? <span className="text-white/35">{relativeTimestamp}</span> : null}
                                      </div>
                                      {content ? <div className="mt-1 whitespace-pre-wrap break-words text-white/80">{content}</div> : null}
                                      {voiceSummary ? <div className="mt-2 text-[11px] text-cyan-100/70">Voice decision | {voiceSummary}</div> : null}
                                    </div>
                                  );
                                })}
                              </div>
                            ) : null}
                            {!historyLoading && !historyError && !historyItems.length ? (
                              <div className="mt-3 text-white/45">No stored thread history yet.</div>
                            ) : null}
                            <div className="mt-3 flex flex-wrap items-center gap-2">
                              {historyHasMore && historyLimit < THREAD_HISTORY_MAX_LIMIT ? (
                                <button
                                  type="button"
                                  onClick={() => handleLoadMoreThreadHistory(threadId)}
                                  disabled={historyLoading}
                                  className="rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-2 py-1 text-cyan-100 transition-colors hover:bg-cyan-400/15 disabled:opacity-50"
                                >
                                  Load older messages
                                </button>
                              ) : null}
                              <button
                                type="button"
                                onClick={() => setExpandedThreadHistoryId(null)}
                                className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10"
                              >
                                Hide history
                              </button>
                            </div>
                          </div>
                        ) : null}
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            onClick={() => handleToggleThreadHistory(threadId)}
                            disabled={!threadId || historyLoading}
                            className="rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-2 py-1 text-cyan-100 transition-colors hover:bg-cyan-400/15 disabled:opacity-50"
                          >
                            {historyExpanded ? "Hide History" : "Open History"}
                          </button>
                          <button
                            type="button"
                            onClick={() => handleThreadAction(threadId, "reset")}
                            disabled={!threadId || busyAction === `thread:reset:${threadId}`}
                            className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                          >
                            Reset
                          </button>
                          <button
                            type="button"
                            onClick={() => handleThreadAction(threadId, "archive")}
                            disabled={!threadId || busyAction === `thread:archive:${threadId}`}
                            className="rounded-lg border border-red-400/20 bg-red-400/10 px-2 py-1 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                          >
                            Archive
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </SectionCard>
            ) : null}

            {jobs ? (
              <SectionCard icon={<Clock3 className="h-4 w-4" />} title="Jobs" status={jobs.status}>
                {jobs.summary || jobs.next_due_summary || jobs.attention_summary ? (
                  <div className="mb-3 text-xs">
                    {jobs.summary ? <div className="text-white/75">{String(jobs.summary)}</div> : null}
                    {jobs.next_due_summary ? <div className="mt-1 text-white/45">{String(jobs.next_due_summary)}</div> : null}
                    {jobs.attention_summary ? (
                      <div className="mt-1 text-amber-100/70">{String(jobs.attention_summary)}</div>
                    ) : null}
                  </div>
                ) : null}
                <div className="grid grid-cols-4 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Total</div>
                    <div className="mt-1 text-white/90">{jobs.counts.total}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Active</div>
                    <div className="mt-1 text-white/90">{jobs.counts.active}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Scheduled</div>
                    <div className="mt-1 text-white/90">{jobs.counts.scheduled}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Failed</div>
                    <div className="mt-1 text-white/90">{jobs.counts.failed}</div>
                  </div>
                </div>
                <div className="mt-3 space-y-2 text-xs">
                  {(jobs.items || []).slice(0, 5).map((job) => {
                    const jobId = String(job.job_id || "");
                    const status = String(job.status || "queued");
                    const jobType = String(job.job_type || "");
                    const lastReason = formatReason(job.last_transition_reason);
                    const lastError = String(job.last_error || "");
                    const canCancel = ["scheduled", "queued", "running", "waiting"].includes(status) && ["deferred_action", "agent_delivery"].includes(jobType);
                    const canRequeue = ["failed", "cancelled", "timed_out", "skipped"].includes(status) && ["deferred_action", "agent_delivery"].includes(jobType);
                    const canSnooze = jobType === "deferred_action" && canCancel;
                    return (
                      <div key={jobId} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-white/85">{formatJobLabel(job)}</span>
                          <span className={statusTone(status)}>{status}</span>
                        </div>
                        <div className="mt-1 truncate text-white/45">{String(job.request_text || jobId)}</div>
                        <div className="mt-2 space-y-1 text-[11px] text-white/45">
                          <div>
                            Attempts {Number(job.attempt_count || 0)}
                            {lastReason ? ` • ${lastReason}` : ""}
                            {job.metadata?.recovered_after_restart ? " • recovered" : ""}
                          </div>
                          {lastError ? <div className="break-words text-red-200/80">{lastError}</div> : null}
                          {job.retry_of_job_id ? <div>Retry of {String(job.retry_of_job_id)}</div> : null}
                          {job.replacement_job_id ? <div>Replaced by {String(job.replacement_job_id)}</div> : null}
                        </div>
                        <div className="mt-2 flex items-center gap-2">
                          {canSnooze ? (
                            <button
                              type="button"
                              onClick={() => handleJobAction(jobId, "snooze")}
                              disabled={busyAction === `job-snooze:${jobId}`}
                              className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                            >
                              Snooze 15m
                            </button>
                          ) : null}
                          {canCancel ? (
                            <button
                              type="button"
                              onClick={() => handleJobAction(jobId, "cancel")}
                              disabled={busyAction === `job-cancel:${jobId}`}
                              className="rounded-lg border border-red-400/20 bg-red-400/10 px-2 py-1 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                            >
                              Cancel
                            </button>
                          ) : null}
                          {canRequeue ? (
                            <button
                              type="button"
                              onClick={() => handleJobAction(jobId, "requeue")}
                              disabled={busyAction === `job-requeue:${jobId}`}
                              className="rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-2 py-1 text-cyan-100 transition-colors hover:bg-cyan-400/15 disabled:opacity-50"
                            >
                              Requeue
                            </button>
                          ) : null}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </SectionCard>
            ) : null}

            {runs ? (
              <SectionCard icon={<Bot className="h-4 w-4" />} title="Runs" status={runs.status}>
                {runs.summary || runs.latest_activity_summary || runs.attention_summary ? (
                  <div className="mb-3 text-xs">
                    {runs.summary ? <div className="text-white/75">{String(runs.summary)}</div> : null}
                    {runs.latest_activity_summary ? (
                      <div className="mt-1 text-white/45">{String(runs.latest_activity_summary)}</div>
                    ) : null}
                    {runs.attention_summary ? (
                      <div className="mt-1 text-amber-100/70">{String(runs.attention_summary)}</div>
                    ) : null}
                  </div>
                ) : null}
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Live Runs</div>
                    <div className="mt-1 text-white/90">{runs.counts.live_runs}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Deliveries</div>
                    <div className="mt-1 text-white/90">{runs.counts.deliveries}</div>
                    {Number(runs.counts.active_deliveries || 0) > 0 || Number(runs.counts.failed_deliveries || 0) > 0 ? (
                      <div className="mt-1 text-[11px] text-white/45">
                        Active {Number(runs.counts.active_deliveries || 0)}
                        {Number(runs.counts.failed_deliveries || 0) > 0
                          ? ` • Failed ${Number(runs.counts.failed_deliveries || 0)}`
                          : ""}
                      </div>
                    ) : null}
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Confirmations</div>
                    <div className="mt-1 text-white/90">{runs.counts.pending_confirmations}</div>
                  </div>
                </div>
                <div className="mt-3 space-y-2 text-xs">
                  {(runs.live_runs || []).slice(0, 3).map((run, index) => (
                    <div key={String(run.run_id || `run-${index}`)} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-white/80">{String(run.run_id || "").slice(0, 10)}</span>
                        <span className={statusTone(String(run.status || "running"))}>{String(run.status || "running")}</span>
                      </div>
                      <div className="mt-1 truncate text-white/55">{String(run.goal || "No goal text")}</div>
                    </div>
                  ))}
                  {(runs.deliveries || []).slice(0, 2).map((delivery, index) => (
                    <div key={String(delivery.delivery_id || `delivery-${index}`)} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-white/85">Delivery</span>
                        <span className={statusTone(String(delivery.status || "queued"))}>{String(delivery.status || "queued")}</span>
                      </div>
                      <div className="mt-1 truncate text-white/45">{String(delivery.request_text || "")}</div>
                      <div className="mt-2 space-y-1 text-[11px] text-white/45">
                        {delivery.child_run_id ? <div>Child run {String(delivery.child_run_id)}</div> : null}
                        {delivery.retry_of_delivery_id ? <div>Retry of {String(delivery.retry_of_delivery_id)}</div> : null}
                        {delivery.replacement_delivery_id ? <div>Replaced by {String(delivery.replacement_delivery_id)}</div> : null}
                        {delivery.metadata?.recovered_after_restart ? <div>Recovered after restart</div> : null}
                        {delivery.error ? <div className="break-words text-red-200/80">{String(delivery.error)}</div> : null}
                      </div>
                    </div>
                  ))}
                </div>
              </SectionCard>
            ) : null}

            {agent ? (
              <SectionCard icon={<Bot className="h-4 w-4" />} title="Agent Runtime" status={agent.status}>
                {agent.summary || agent.current_focus_summary || agent.attention_summary ? (
                  <div className="mb-3 text-xs">
                    {agent.summary ? <div className="text-white/75">{String(agent.summary)}</div> : null}
                    {agent.current_focus_summary ? (
                      <div className="mt-1 text-white/45">{String(agent.current_focus_summary)}</div>
                    ) : null}
                    {agent.attention_summary ? (
                      <div className="mt-1 text-amber-100/70">{String(agent.attention_summary)}</div>
                    ) : null}
                  </div>
                ) : null}
                <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Roots</div>
                    <div className="mt-1 text-white/90">{agent.counts.roots}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Live Runs</div>
                    <div className="mt-1 text-white/90">{agent.counts.live_runs}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Confirmations</div>
                    <div className="mt-1 text-white/90">{agent.counts.pending_confirmations}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Deliveries</div>
                    <div className="mt-1 text-white/90">{agent.counts.deliveries}</div>
                    {Number(agent.counts.active_deliveries || 0) > 0 || Number(agent.counts.failed_deliveries || 0) > 0 ? (
                      <div className="mt-1 text-[11px] text-white/45">
                        Active {Number(agent.counts.active_deliveries || 0)}
                        {Number(agent.counts.failed_deliveries || 0) > 0
                          ? ` | Failed ${Number(agent.counts.failed_deliveries || 0)}`
                          : ""}
                      </div>
                    ) : null}
                  </div>
                </div>
                <div className="mt-3 space-y-3 text-xs">
                  {agentRoots.length > 0 ? (
                    <div>
                      <div className="mb-2 text-[11px] uppercase tracking-widest text-white/40">Roots</div>
                      <div className="space-y-2">
                        {agentRoots.slice(0, 3).map((root, index) => {
                          const rootLabel = formatAgentRootLabel(root);
                          const rootStatus = String(root.status || (root.active_run_id ? "active" : "ready"));
                          const rootActivity = formatAgentRootActivity(root, agentRunsById);
                          const rootContext = formatAgentRootContext(root);
                          const rootUpdatedAt = Number(root.last_activity || root.updated_at || 0);
                          const showUpdated = Number.isFinite(rootUpdatedAt)
                            && rootUpdatedAt > 0
                            && !rootActivity.toLowerCase().startsWith("updated ");
                          return (
                            <div
                              key={String(root.session_key || `root-${index}`)}
                              className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2"
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="truncate text-white/85">{rootLabel}</span>
                                <span className={statusTone(rootStatus)}>{rootStatus}</span>
                              </div>
                              <div className="mt-1 text-white/55">{rootActivity}</div>
                              <div className="mt-2 space-y-1 text-[11px] text-white/45">
                                {rootContext ? <div>{rootContext}</div> : null}
                                {showUpdated ? <div>Updated {formatRelative(rootUpdatedAt)}</div> : null}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}

                  {agentLiveRuns.length > 0 ? (
                    <div>
                      <div className="mb-2 text-[11px] uppercase tracking-widest text-white/40">Live Runs</div>
                      <div className="space-y-2">
                        {agentLiveRuns.slice(0, 3).map((run, index) => {
                          const runStatus = String(run.status || "running");
                          const runRoot = resolveAgentRootForRun(run, agentRootsBySessionKey, agentRootsByRunId);
                          const runRootLabel = runRoot ? formatAgentRootLabel(runRoot) : "";
                          const runUpdatedAt = Number(run.updated_at || run.created_at || 0);
                          return (
                            <div
                              key={String(run.run_id || `run-${index}`)}
                              className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2"
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="font-mono text-white/80">{String(run.run_id || "").slice(0, 10)}</span>
                                <span className={statusTone(runStatus)}>{runStatus}</span>
                              </div>
                              <div className="mt-1 truncate text-white/55">{String(run.goal || run.summary || "No goal text")}</div>
                              <div className="mt-2 space-y-1 text-[11px] text-white/45">
                                {runRootLabel ? <div>{runRootLabel}</div> : null}
                                {Number.isFinite(runUpdatedAt) && runUpdatedAt > 0 ? <div>Updated {formatRelative(runUpdatedAt)}</div> : null}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}

                  {agentDeliveries.length > 0 ? (
                    <div>
                      <div className="mb-2 text-[11px] uppercase tracking-widest text-white/40">Deliveries</div>
                      <div className="space-y-2">
                        {agentDeliveries.slice(0, 3).map((delivery, index) => {
                          const deliveryStatus = String(delivery.status || "queued");
                          const deliveryRoot = agentRootsBySessionKey.get(String(delivery.root_session_key || "").trim()) || null;
                          const deliveryRootLabel = deliveryRoot ? formatAgentRootLabel(deliveryRoot) : "";
                          const deliveryUpdatedAt = Number(delivery.updated_at || delivery.created_at || 0);
                          return (
                            <div
                              key={String(delivery.delivery_id || `agent-delivery-${index}`)}
                              className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2"
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-white/85">Delivery</span>
                                <span className={statusTone(deliveryStatus)}>{deliveryStatus}</span>
                              </div>
                              <div className="mt-1 truncate text-white/55">
                                {String(delivery.request_text || delivery.response_text || "Agent delivery")}
                              </div>
                              <div className="mt-2 space-y-1 text-[11px] text-white/45">
                                {deliveryRootLabel ? <div>{deliveryRootLabel}</div> : null}
                                {delivery.child_run_id ? <div>Child run {String(delivery.child_run_id).slice(0, 10)}</div> : null}
                                {delivery.retry_of_delivery_id ? <div>Retry of {String(delivery.retry_of_delivery_id)}</div> : null}
                                {delivery.replacement_delivery_id ? <div>Replaced by {String(delivery.replacement_delivery_id)}</div> : null}
                                {delivery.metadata?.recovered_after_restart ? <div>Recovered after restart</div> : null}
                                {Number.isFinite(deliveryUpdatedAt) && deliveryUpdatedAt > 0 ? <div>Updated {formatRelative(deliveryUpdatedAt)}</div> : null}
                                {delivery.error ? <div className="break-words text-red-200/80">{String(delivery.error)}</div> : null}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}

                  {agentConfirmations.length > 0 ? (
                    <div>
                      <div className="mb-2 text-[11px] uppercase tracking-widest text-white/40">Pending Confirmations</div>
                      <div className="space-y-2">
                        {agentConfirmations.slice(0, 3).map((confirmation, index) => {
                          const confirmationRunId = String(confirmation.run_id || "").trim();
                          const confirmationMetadata = confirmation.metadata
                            && typeof confirmation.metadata === "object"
                            && !Array.isArray(confirmation.metadata)
                            ? confirmation.metadata as Record<string, any>
                            : {};
                          const confirmationRoot = agentRootsByRunId.get(confirmationRunId)
                            || agentRootsBySessionKey.get(String(confirmationMetadata.root_session_key || "").trim())
                            || null;
                          const confirmationRootLabel = confirmationRoot ? formatAgentRootLabel(confirmationRoot) : "";
                          const expiresAt = Number(confirmation.expires_at || 0);
                          const createdAt = Number(confirmation.created_at || 0);
                          return (
                            <div
                              key={String(confirmation.token || `confirmation-${index}`)}
                              className="rounded-xl border border-amber-400/15 bg-amber-400/8 px-3 py-2 text-amber-100/85"
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="truncate text-amber-50">{formatAgentConfirmationSummary(confirmation)}</span>
                                <span className={statusTone(String(confirmation.status || "pending"))}>
                                  {String(confirmation.status || "pending")}
                                </span>
                              </div>
                              <div className="mt-2 space-y-1 text-[11px] text-amber-100/70">
                                {confirmationRootLabel ? <div>{confirmationRootLabel}</div> : null}
                                {Number.isFinite(expiresAt) && expiresAt > 0 ? <div>Expires {formatTimestamp(expiresAt)}</div> : null}
                                {!expiresAt && Number.isFinite(createdAt) && createdAt > 0 ? <div>Created {formatRelative(createdAt)}</div> : null}
                                {confirmation.token ? <div className="font-mono text-amber-50/80">{String(confirmation.token).slice(0, 10)}</div> : null}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                </div>
              </SectionCard>
            ) : null}

            {voice ? (
              <SectionCard
                icon={<Mic className="h-4 w-4" />}
                title="Voice Runtime"
                status={voice.status}
                anchorId="voice-runtime"
              >
                <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-3 xl:grid-cols-5">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Active Local</div>
                    <div className="mt-1 text-white/90">{localVoiceActive ? "Yes" : "No"}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Browser Sessions</div>
                    <div className="mt-1 text-white/90">{Number(voice.counts?.browser_sessions || browserVoiceSessions.length)}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Direct Browser</div>
                    <div className={`mt-1 ${Number(voice.counts?.browser_direct_active_sessions || 0) > 0 ? "text-emerald-300" : "text-white/90"}`}>
                      {Number(voice.counts?.browser_direct_active_sessions || 0)}
                    </div>
                    <div className="mt-1 text-white/45">
                      Recent {Number(voice.counts?.browser_direct_sessions || 0)}
                    </div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Discord Sessions</div>
                    <div className="mt-1 text-white/90">{Number(voice.counts?.discord_sessions || voice.active_discord_sessions.length)}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Fallbacks</div>
                    <div className={`mt-1 ${Number(voice.counts?.fallback_sessions || fallbackVoiceSessions.length) > 0 ? "text-amber-200" : "text-white/90"}`}>
                      {Number(voice.counts?.fallback_sessions || fallbackVoiceSessions.length)}
                    </div>
                  </div>
                </div>
                <div className="mt-3 grid gap-3 xl:grid-cols-2">
                  <ControlSurfaceCard
                    title="Local Talk Posture"
                    status={localVoiceStatus}
                    summary={
                      localVoiceActive
                        ? "Saved local talk defaults compared with the live local runtime."
                        : "Saved posture for the next local talk session."
                    }
                    configuredLabel="Saved default"
                    runningLabel={localVoiceActive ? "Live session" : "Running"}
                    configuredValue={formatVoiceRuntimeProfileSummary(savedLocalVoiceProfile)}
                    runningValue={localVoiceActive ? formatVoiceRuntimeProfileSummary(activeLocalVoiceDisplayProfile) : "No live local voice session"}
                    attention={localVoiceAttention || undefined}
                    details={
                      <>
                        <div>
                          Group voice default: {formatReason(voice.saved_defaults.group_voice_behavior || "socially_adaptive") || "socially adaptive"}
                        </div>
                        <div>
                          {activeLocalVoiceSession
                            ? `Current lane ${voiceSessionSurfaceLabel(activeLocalVoiceSession)} | ${formatReason(String(activeLocalVoiceSession.floor_state || activeLocalVoiceSession.state || "idle")) || "idle"} | updated ${formatRelative(activeLocalVoiceSession.last_event_at || mission.generated_at)}`
                            : "Start local talk mode to patch the live runtime here."}
                        </div>
                        {localVoicePendingChanges.length ? (
                          <div className="text-cyan-100/70">
                            Pending local patch changes {formatRuntimeLabelList(localVoicePendingChanges)}.
                          </div>
                        ) : null}
                      </>
                    }
                  />
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-white/40">Local Talk Mode</div>
                      <div className={statusTone(String(activeLocalVoiceSession?.floor_state || activeLocalVoiceSession?.state || "idle"))}>
                        {String(activeLocalVoiceSession?.floor_state || activeLocalVoiceSession?.state || "idle")}
                      </div>
                    </div>
                    <div className="mt-2 text-white/55">
                      {voice.control_capabilities?.can_patch_local_runtime
                        ? "Patch the live local runtime here without targeting the raw session key."
                        : "Start local talk mode to adjust the live local runtime here."}
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-2">
                      <label className="space-y-1">
                        <div className="text-white/45">Engine</div>
                        <select
                          value={localVoiceDraft.voice_engine}
                          onChange={(event) => setLocalVoiceDraftField("voice_engine", event.target.value)}
                          disabled={!voice.control_capabilities?.can_patch_local_runtime}
                          className="w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-white outline-none disabled:opacity-50"
                        >
                          {localVoiceEngineOptions.map((option) => (
                            <option key={option.id} value={option.id} className="bg-[#090b14]">
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      {[
                        { key: "interaction_mode", label: "Interaction", options: ["direct_address", "passive"] },
                        { key: "presence_profile", label: "Presence", options: ["quiet", "balanced", "social"] },
                        { key: "focus_state", label: "Focus", options: ["responsive", "quiet"] },
                        { key: "local_transport_mode", label: "Local Mode", options: ["push_to_talk", "passive", "quiet"] },
                      ].map((field) => (
                        <label key={field.key} className="space-y-1">
                          <div className="text-white/45">{field.label}</div>
                          <select
                            value={localVoiceDraft[field.key as keyof VoiceDraft]}
                            onChange={(event) => setLocalVoiceDraftField(field.key as keyof VoiceDraft, event.target.value)}
                            disabled={!voice.control_capabilities?.can_patch_local_runtime}
                            className="w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-white outline-none disabled:opacity-50"
                          >
                            {field.options.map((option) => (
                              <option key={option} value={option} className="bg-[#090b14]">
                                {option}
                              </option>
                            ))}
                          </select>
                        </label>
                      ))}
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-2">
                      <div className="text-white/45">
                        {activeLocalVoiceSession
                          ? `Current lane ${voiceSessionSurfaceLabel(activeLocalVoiceSession)}`
                          : "No live local voice session in this process."}
                      </div>
                      <button
                        type="button"
                        onClick={handleLocalVoiceRuntimeApply}
                        disabled={!voice.control_capabilities?.can_patch_local_runtime || busyAction === "voice-local-apply"}
                        className="rounded-xl border border-cyan-400/20 bg-cyan-400/10 px-3 py-2 text-cyan-100 transition-colors hover:bg-cyan-400/15 disabled:opacity-50"
                      >
                        {busyAction === "voice-local-apply" ? "Applying..." : "Apply Local Runtime"}
                      </button>
                    </div>
                  </div>
                </div>
                <div className="mt-3 space-y-3 text-xs">
                  {liveVoiceSessions.length ? (
                    liveVoiceSessions.map((session) => {
                      const sessionKey = String(session.runtime_session_key || "");
                      const draft = voiceDrafts[sessionKey] || {
                        voice_engine: String(session.voice_engine_requested || session.voice_engine || DEFAULT_VOICE_DRAFT.voice_engine),
                        interaction_mode: String(session.interaction_mode || DEFAULT_VOICE_DRAFT.interaction_mode),
                        presence_profile: String(session.presence_profile || DEFAULT_VOICE_DRAFT.presence_profile),
                        focus_state: String(session.focus_state || DEFAULT_VOICE_DRAFT.focus_state),
                        local_transport_mode: String(session.local_transport_mode || DEFAULT_VOICE_DRAFT.local_transport_mode),
                      };
                      const platform = String(session.platform || "").trim().toLowerCase();
                      const isLocal = platform === "local";
                      const effectiveEngine =
                        String(session.voice_engine_resolution?.effective_label || session.voice_engine || "classic");
                      const requestedEngine =
                        String(session.voice_engine_resolution?.requested_label || session.voice_engine_requested || session.voice_engine || "classic");
                      const fallbackReason =
                        String(
                          session.voice_engine_resolution?.fallback_reason_label
                          || formatReason(session.voice_engine_fallback_reason)
                          || "unknown",
                        );
                      const decisionTrace = session.turn_decision_trace as
                        | {
                            behavior_label?: string;
                            summary?: string;
                            decision_reason_label?: string;
                            initiative_source_label?: string;
                            reply_posture_label?: string;
                            confidence?: number;
                            confidence_label?: string;
                            should_mirror_text?: boolean;
                            mirror_policy_label?: string;
                            identity_cues?: Array<{
                              key: string;
                              label: string;
                              value: string;
                              value_label?: string;
                            }>;
                          }
                        | undefined;
                      return (
                        <div key={sessionKey} className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <div className="text-sm text-white/90">
                                {voiceSessionSurfaceLabel(session)}
                              </div>
                              <div className="mt-1 font-mono text-[11px] text-white/40">{sessionKey}</div>
                            </div>
                            <div className={`text-[11px] uppercase tracking-widest ${statusTone(String(session.floor_state || session.state || "idle"))}`}>
                              {String(session.floor_state || session.state || "idle")}
                            </div>
                          </div>
                          <div className="mt-3 grid grid-cols-2 gap-2 text-white/75">
                            <div>Presence: {session.presence_profile}</div>
                            <div>Focus: {session.focus_state}</div>
                            <div>Interaction: {session.interaction_mode}</div>
                            <div>Engine: {effectiveEngine}</div>
                            <div>Passive: {String(Boolean(session.passive_mode))}</div>
                            <div>Requested: {requestedEngine}</div>
                            <div>Context: {session.social_context || "unknown"}</div>
                            <div>Participants: {`${session.human_participant_count ?? 0} human / ${session.bot_participant_count ?? 0} bot`}</div>
                            <div>Owner Present: {String(Boolean(session.owner_present))}</div>
                            <div>Group Voice: {session.group_voice_behavior || "socially_adaptive"}</div>
                            <div>Last Decision: {session.last_decision_reason || "none"}</div>
                            <div>Initiative: {session.initiative_source || "none"}</div>
                            <div>Reply Posture: {session.reply_posture || "brief_companion"}</div>
                            <div>Repair: {session.last_repair_reason || "none"}</div>
                            <div>Interrupted: {String(Boolean(session.last_response_interrupted))}</div>
                          </div>
                          {(() => {
                            const sessionConfiguredProfile: VoiceRuntimeProfile = {
                              voice_engine: savedLocalVoiceProfile.voice_engine,
                              interaction_mode: savedLocalVoiceProfile.interaction_mode,
                              presence_profile: savedLocalVoiceProfile.presence_profile,
                              focus_state: savedLocalVoiceProfile.focus_state,
                              local_transport_mode: isLocal ? savedLocalVoiceProfile.local_transport_mode : undefined,
                            };
                            const sessionRunningProfile: VoiceRuntimeProfile = {
                              voice_engine: String(session.voice_engine || session.voice_engine_requested || sessionConfiguredProfile.voice_engine),
                              interaction_mode: String(session.interaction_mode || sessionConfiguredProfile.interaction_mode),
                              presence_profile: String(session.presence_profile || sessionConfiguredProfile.presence_profile),
                              focus_state: String(session.focus_state || sessionConfiguredProfile.focus_state),
                              local_transport_mode: isLocal
                                ? String(session.local_transport_mode || sessionConfiguredProfile.local_transport_mode || "")
                                : undefined,
                            };
                            const sessionDrift = collectVoiceRuntimeProfileDifferences(
                              sessionConfiguredProfile,
                              sessionRunningProfile,
                            ).filter((label) => !(session.voice_engine_fallback_active && label === "engine"));
                            if (!sessionDrift.length) return null;
                            return (
                              <div className="mt-3 rounded-xl border border-white/10 bg-black/20 px-3 py-2 text-white/70">
                                {isLocal
                                  ? `Saved defaults differ from this live local runtime on ${formatRuntimeLabelList(sessionDrift)}. Use the Local Talk Mode controls above if you want the live session to match.`
                                  : `Saved defaults differ from this session on ${formatRuntimeLabelList(sessionDrift)}. Use Apply if you want the live session to match.`}
                              </div>
                            );
                          })()}
                          {session.voice_engine_fallback_active ? (
                            <div className="mt-3 rounded-xl border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-amber-100/85">
                              <div className="font-medium">Voice engine fallback active</div>
                              <div className="mt-1 text-amber-100/70">
                                Requested {requestedEngine}, running {effectiveEngine}. Reason: {fallbackReason}.
                              </div>
                            </div>
                          ) : null}
                          {decisionTrace ? (
                            <div className="mt-3 rounded-xl border border-cyan-400/15 bg-cyan-400/[0.06] px-3 py-3 text-white/80">
                              <div className="text-[11px] uppercase tracking-[0.18em] text-cyan-100/55">Decision Trace</div>
                              <div className="mt-2 text-sm text-white/90">
                                {decisionTrace.behavior_label || "Decision recorded"}
                              </div>
                              <div className="mt-1 text-white/65">
                                {decisionTrace.summary || "Recent voice decision recorded for inspectability."}
                              </div>
                              <div className="mt-2 grid grid-cols-2 gap-2 text-white/65">
                                <div>Reason: {decisionTrace.decision_reason_label || "none"}</div>
                                <div>Source: {decisionTrace.initiative_source_label || "none"}</div>
                                <div>Posture: {decisionTrace.reply_posture_label || "brief companion"}</div>
                                <div>
                                  Confidence: {decisionTrace.confidence_label || (
                                    typeof decisionTrace.confidence === "number"
                                      ? `${Math.round(Math.max(Math.min(decisionTrace.confidence, 1), 0) * 100)}%`
                                      : "n/a"
                                  )}
                                </div>
                                <div>Mirror: {decisionTrace.mirror_policy_label || "unchanged"}</div>
                              </div>
                              {decisionTrace.identity_cues?.length ? (
                                <div className="mt-3 flex flex-wrap gap-2">
                                  {decisionTrace.identity_cues.slice(0, 6).map((cue) => (
                                    <span
                                      key={`${cue.key}:${cue.value}`}
                                      className="rounded-full border border-cyan-200/15 bg-black/20 px-2 py-1 text-[11px] text-cyan-50/80"
                                    >
                                      {cue.label}: {cue.value_label || cue.value}
                                    </span>
                                  ))}
                                </div>
                              ) : null}
                            </div>
                          ) : null}
                          {(session.last_user_excerpt || session.last_assistant_excerpt) ? (
                            <div className="mt-3 grid gap-2 text-white/60">
                              {session.last_user_excerpt ? <div>User: {session.last_user_excerpt}</div> : null}
                              {session.last_assistant_excerpt ? <div>PaLLi: {session.last_assistant_excerpt}</div> : null}
                            </div>
                          ) : null}
                          {isLocal ? (
                            <div className="mt-3 rounded-xl border border-cyan-400/15 bg-cyan-400/[0.06] px-3 py-2 text-white/70">
                              Use the Local Talk Mode controls above to change the live local runtime without targeting this raw session key.
                            </div>
                          ) : (
                            <div className="mt-3 grid grid-cols-2 gap-2">
                              {[
                                { key: "interaction_mode", label: "Interaction", options: ["direct_address", "passive"] },
                                { key: "presence_profile", label: "Presence", options: ["quiet", "balanced", "social"] },
                                { key: "focus_state", label: "Focus", options: ["responsive", "quiet"] },
                              ].map((field) => (
                                <label key={field.key} className="space-y-1">
                                  <div className="text-white/45">{field.label}</div>
                                  <select
                                    value={draft[field.key as keyof VoiceDraft]}
                                    onChange={(event) => setVoiceDraftField(sessionKey, field.key as keyof VoiceDraft, event.target.value)}
                                    className="w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-white outline-none"
                                  >
                                    {field.options.map((option) => (
                                      <option key={option} value={option} className="bg-[#090b14]">
                                        {option}
                                      </option>
                                    ))}
                                  </select>
                                </label>
                              ))}
                            </div>
                          )}
                          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                            <div className="text-white/45">
                              Updated {formatRelative(session.last_event_at || mission.generated_at)}
                            </div>
                            <div className="flex flex-wrap gap-2">
                              {!isLocal ? (
                                <button
                                  type="button"
                                  onClick={() => handleVoiceApply(sessionKey)}
                                  disabled={busyAction === `voice-apply:${sessionKey}`}
                                  className="rounded-xl border border-cyan-400/20 bg-cyan-400/10 px-3 py-2 text-cyan-100 transition-colors hover:bg-cyan-400/15 disabled:opacity-50"
                                >
                                  {busyAction === `voice-apply:${sessionKey}` ? "Applying..." : "Apply"}
                                </button>
                              ) : null}
                              <button
                                type="button"
                                onClick={() => handleVoiceSessionAction(sessionKey, "abort")}
                                disabled={!session.control_capabilities?.can_abort || busyAction === `voice-abort:${sessionKey}`}
                                className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-white/80 transition-colors hover:bg-white/10 disabled:opacity-50"
                              >
                                {busyAction === `voice-abort:${sessionKey}` ? "Aborting..." : "Abort"}
                              </button>
                              <button
                                type="button"
                                onClick={() => handleVoiceSessionAction(sessionKey, "stop")}
                                disabled={!session.control_capabilities?.can_stop || busyAction === `voice-stop:${sessionKey}`}
                                className="rounded-xl border border-red-400/20 bg-red-400/10 px-3 py-2 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                              >
                                {busyAction === `voice-stop:${sessionKey}` ? "Stopping..." : "Stop"}
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })
                  ) : (
                    <div className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2 text-white/55">
                      No live voice session is active in this process.
                    </div>
                  )}
                </div>
              </SectionCard>
            ) : null}

            {context ? (
              <SectionCard icon={<House className="h-4 w-4" />} title="Home Context" status={context.status}>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Presence</div>
                    <div className="mt-1 text-white/90">{formatContextState(context.state.owner_presence_state)}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Privacy</div>
                    <div className="mt-1 text-white/90">{formatContextState(context.state.privacy_state)}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Machine</div>
                    <div className="mt-1 text-white/90">{formatContextState(context.state.machine_state)}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Quiet Hours</div>
                    <div className="mt-1 text-white/90">{context.state.quiet_hours_active ? "Active" : "Off"}</div>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  {[
                    "owner_arrived",
                    "owner_departed",
                    "machine_unlocked",
                    "machine_locked",
                    "machine_active",
                    "machine_idle",
                    "privacy_private",
                    "privacy_shared",
                  ].map((eventType) => (
                    <button
                      key={eventType}
                      type="button"
                      onClick={() => handleContextSimulation(eventType)}
                      disabled={busyAction === `context-simulate:${eventType}`}
                      className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-white/75 transition-colors hover:bg-white/10 disabled:opacity-50"
                    >
                      {busyAction === `context-simulate:${eventType}` ? "Running..." : formatReason(eventType)}
                    </button>
                  ))}
                </div>
                <div className="mt-3 space-y-2 text-xs">
                  {Object.entries(context.source_health || {}).map(([source, details]) => (
                    <div key={source} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-white/85">{formatReason(source)}</span>
                        <span className={statusTone(String((details as Record<string, any>)?.status || "ready"))}>
                          {String((details as Record<string, any>)?.status || "ready")}
                        </span>
                      </div>
                      <div className="mt-1 text-white/45">{String((details as Record<string, any>)?.message || "ok")}</div>
                    </div>
                  ))}
                  {(context.recent_events || []).slice(0, 4).map((event) => (
                    <div key={String(event.event_id || event.occurred_at)} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-white/85">{formatReason(String(event.event_type || ""))}</span>
                        <span className="text-white/45">{formatRelative(event.occurred_at)}</span>
                      </div>
                      <div className="mt-1 text-white/45">{String(event.source || "unknown source")}</div>
                    </div>
                  ))}
                </div>
              </SectionCard>
            ) : null}

            {ambient ? (
              <SectionCard icon={<Sparkles className="h-4 w-4" />} title="Ambient Actions" status={ambient.status}>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Recent</div>
                    <div className="mt-1 text-white/90">{ambient.counts.recent_actions}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Completed</div>
                    <div className="mt-1 text-white/90">{ambient.counts.completed}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Suppressed</div>
                    <div className="mt-1 text-white/90">{ambient.counts.suppressed}</div>
                  </div>
                </div>
                <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] p-3 text-xs text-white/70">
                  Cooldown{" "}
                  {ambient.cooldown?.active
                    ? `active for ${Math.max(1, Math.round(Number(ambient.cooldown?.remaining_seconds || 0) / 60))}m`
                    : "inactive"}
                </div>
                <div className="mt-3 space-y-2 text-xs">
                  {(ambient.recent_actions || []).slice(0, 4).map((action) => {
                    const usefulItemSummary = String(action.useful_item_summary || "").trim();
                    const generatedTextPreview = String(
                      action.generated_text_preview || action.content?.dashboard_text || action.content?.spoken_text || "",
                    ).trim();
                    return (
                      <div key={String(action.action_id || action.updated_at)} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-white/85">{formatReason(String(action.action_type || "ambient_action"))}</span>
                          <span className={statusTone(String(action.status || "ready"))}>{String(action.status || "ready")}</span>
                        </div>
                        <div className="mt-1 text-white/45">
                          {String(action.delivery_surface || "dashboard")}
                          {action.decision_reason ? ` ? ${formatReason(String(action.decision_reason || ""))}` : ""}
                          {action.suppression_reason ? ` ? ${formatReason(String(action.suppression_reason || ""))}` : ""}
                        </div>
                        {usefulItemSummary ? <div className="mt-2 text-white/60">{usefulItemSummary}</div> : null}
                        {generatedTextPreview ? <div className="mt-1 text-white/70">{generatedTextPreview}</div> : null}
                      </div>
                    );
                  })}
                </div>
              </SectionCard>
            ) : null}

            {operator ? (
              <SectionCard icon={<TerminalSquare className="h-4 w-4" />} title="Operator" status={operator.status}>
                <div className="grid grid-cols-4 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Recent</div>
                    <div className="mt-1 text-white/90">{operator.counts.recent_actions}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Active</div>
                    <div className="mt-1 text-white/90">{operator.counts.active_actions}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Approvals</div>
                    <div className="mt-1 text-white/90">{operator.counts.pending_confirmations}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Blocked</div>
                    <div className="mt-1 text-white/90">{operator.counts.blocked_actions}</div>
                  </div>
                </div>
                <div className="mt-3 grid grid-cols-4 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Workflows</div>
                    <div className="mt-1 text-white/90">{operator.counts.recent_workflows || 0}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Active WF</div>
                    <div className="mt-1 text-white/90">{operator.counts.active_workflows || 0}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Waiting WF</div>
                    <div className="mt-1 text-white/90">{operator.counts.waiting_workflows || 0}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Paused WF</div>
                    <div className="mt-1 text-white/90">{operator.counts.paused_workflows || 0}</div>
                  </div>
                </div>
                <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Memories</div>
                    <div className="mt-1 text-white/90">{operator.counts.recent_memories || 0}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Open Followthrough</div>
                    <div className="mt-1 text-white/90">{operator.counts.open_followthrough || 0}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Identity Sync</div>
                    <div className="mt-1 text-white/90">{String(operator.identity_sync_status || "idle")}</div>
                  </div>
                </div>
                {currentWorkflow ? (
                  <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] p-3 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-white/40">Current Workflow</div>
                      <span className={statusTone(String(currentWorkflow.status || "active"))}>
                        {String(currentWorkflow.status || "active")}
                      </span>
                    </div>
                    <div className="mt-2 text-white/90">
                      {String(currentWorkflow.objective || currentWorkflow.workflow_id || "Workflow")}
                    </div>
                    <div className="mt-1 text-white/45">
                      {formatReason(String(currentWorkflow.current_phase || "executing"))}
                      {currentWorkflow.wait_state ? ` • ${formatReason(String(currentWorkflow.wait_state || ""))}` : ""}
                      {operator.closure_state ? ` • ${formatReason(String(operator.closure_state || ""))}` : ""}
                    </div>
                    {operator.latest_handoff_summary ? (
                      <div className="mt-2 rounded-lg border border-emerald-400/15 bg-emerald-400/5 px-3 py-2">
                        <div className="text-white/40">Latest Handoff</div>
                        <div className="mt-1 text-white/85">{String(operator.latest_handoff_summary || "")}</div>
                        {operator.latest_what_changed ? (
                          <div className="mt-1 text-white/60">What changed: {String(operator.latest_what_changed || "")}</div>
                        ) : null}
                        {operator.latest_remaining_work ? (
                          <div className="mt-1 text-white/55">Remaining: {String(operator.latest_remaining_work || "")}</div>
                        ) : null}
                        {operator.latest_recommended_action_label ? (
                          <div className="mt-1 text-white/50">Next move: {String(operator.latest_recommended_action_label || "")}</div>
                        ) : null}
                        {operator.last_handoff_at ? (
                          <div className="mt-1 text-white/40">{formatRelative(Number(operator.last_handoff_at || 0))}</div>
                        ) : null}
                      </div>
                    ) : null}
                    {operator.latest_memory ? (
                      <div className="mt-2 rounded-lg border border-cyan-400/15 bg-cyan-400/5 px-3 py-2">
                        <div className="text-white/40">Latest Memory</div>
                        <div className="mt-1 text-white/85">{String(operator.latest_memory.summary || "")}</div>
                        {operator.latest_memory.carry_forward_text ? (
                          <div className="mt-1 text-white/60">
                            Carry forward: {String(operator.latest_memory.carry_forward_text || "")}
                          </div>
                        ) : null}
                        <div className="mt-1 text-white/40">
                          Sync: {String(operator.latest_memory.identity_sync_status || operator.identity_sync_status || "skipped")}
                        </div>
                      </div>
                    ) : null}
                    {operator.latest_followthrough ? (
                      <div className="mt-2 rounded-lg border border-amber-400/20 bg-amber-400/10 px-3 py-2">
                        <div className="text-white/40">Latest Followthrough</div>
                        <div className="mt-1 text-white/85">{String(operator.latest_followthrough.summary || "")}</div>
                        {operator.latest_followthrough.why_now ? (
                          <div className="mt-1 text-white/60">Why now: {String(operator.latest_followthrough.why_now || "")}</div>
                        ) : null}
                        <div className="mt-1 text-white/45">
                          {formatReason(String(operator.latest_followthrough.status || "open"))}
                          {operator.latest_followthrough.recommended_next_action
                            ? ` • ${formatReason(String(operator.latest_followthrough.recommended_next_action || ""))}`
                            : ""}
                          {operator.latest_followthrough.next_review_at
                            ? ` • due ${formatTimestamp(Number(operator.latest_followthrough.next_review_at || 0))}`
                            : ""}
                        </div>
                      </div>
                    ) : null}
                    {operator.progress_summary ? (
                      <div className="mt-2 text-white/75">{String(operator.progress_summary || "")}</div>
                    ) : null}
                    {operator.progress_state ? (
                      <div className="mt-1 text-white/45">
                        Progress: {formatReason(String(operator.progress_state || ""))}
                        {operator.drift_status ? ` • ${formatReason(String(operator.drift_status || ""))}` : ""}
                        {typeof operator.same_step_no_progress_streak === "number" && operator.same_step_no_progress_streak > 0
                          ? ` • no progress streak ${operator.same_step_no_progress_streak}`
                          : ""}
                      </div>
                    ) : null}
                    {operator.latest_meaningful_change ? (
                      <div className="mt-1 text-white/55">Latest change: {String(operator.latest_meaningful_change || "")}</div>
                    ) : null}
                    {operator.next_step_preview ? (
                      <div className="mt-1 text-white/50">Next: {String(operator.next_step_preview || "")}</div>
                    ) : null}
                    {operator.strategy_family || operator.strategy_summary ? (
                      <div className="mt-2 rounded-lg border border-white/5 bg-black/20 px-3 py-2">
                        <div className="text-white/40">Strategy</div>
                        <div className="mt-1 text-white/85">
                          {operator.strategy_family ? formatReason(String(operator.strategy_family || "")) : "Current strategy"}
                          {typeof operator.strategy_attempt_count === "number" && operator.strategy_attempt_count > 0
                            ? ` • attempt ${operator.strategy_attempt_count}`
                            : ""}
                          {operator.strategy_exhausted ? " • exhausted" : ""}
                        </div>
                        {operator.strategy_summary ? (
                          <div className="mt-1 text-white/70">{String(operator.strategy_summary || "")}</div>
                        ) : null}
                        {operator.strategy_last_shift_reason ? (
                          <div className="mt-1 text-white/45">
                            Shift: {formatReason(String(operator.strategy_last_shift_reason || ""))}
                            {operator.strategy_last_shift_at ? ` • ${formatRelative(operator.strategy_last_shift_at)}` : ""}
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                    {operator.blocker_class || operator.blocker_summary ? (
                      <div className="mt-2 rounded-lg border border-white/5 bg-black/20 px-3 py-2">
                        <div className="text-white/40">Blocker</div>
                        <div className="mt-1 text-white/85">
                          {operator.blocker_class ? formatReason(String(operator.blocker_class || "")) : "Blocker"}
                        </div>
                        {operator.blocker_summary ? (
                          <div className="mt-1 text-white/70">{String(operator.blocker_summary || "")}</div>
                        ) : null}
                      </div>
                    ) : null}
                    {operator.current_step?.title ? (
                      <div className="mt-2 rounded-lg border border-white/5 bg-black/20 px-3 py-2">
                        <div className="text-white/40">Current Step</div>
                        <div className="mt-1 text-white/85">{String(operator.current_step.title || "Current step")}</div>
                        <div className="mt-1 text-white/45">
                          {formatReason(String(operator.current_step.step_kind || "step"))}
                          {operator.current_step.status ? ` • ${formatReason(String(operator.current_step.status || ""))}` : ""}
                        </div>
                      </div>
                    ) : null}
                    {operator.current_wait && (operator.current_wait.state || operator.current_wait.prompt || operator.current_wait.payload_summary) ? (
                      <div className="mt-2 rounded-lg border border-white/5 bg-black/20 px-3 py-2">
                        <div className="text-white/40">Waiting</div>
                        <div className="mt-1 text-white/85">
                          {formatReason(String(operator.current_wait.state || "none"))}
                          {operator.current_wait.reason_code ? ` • ${formatReason(String(operator.current_wait.reason_code || ""))}` : ""}
                        </div>
                        {operator.current_wait.prompt ? (
                          <div className="mt-1 text-white/70">{String(operator.current_wait.prompt || "")}</div>
                        ) : operator.current_wait.payload_summary ? (
                          <div className="mt-1 text-white/60">{String(operator.current_wait.payload_summary || "")}</div>
                        ) : null}
                        {operator.current_wait.resume_policy ? (
                          <div className="mt-1 text-white/45">Resume policy: {formatReason(String(operator.current_wait.resume_policy || ""))}</div>
                        ) : null}
                      </div>
                    ) : null}
                    <WorkflowAuditCallout notice={currentWorkflowAudit} className="mt-2" />
                    {operator.last_resume_brief ? (
                      <div className="mt-2 text-white/60">{String(operator.last_resume_brief || "")}</div>
                    ) : null}
                    {operator.last_answer_summary ? (
                      <div className="mt-1 text-white/50">Last answer: {String(operator.last_answer_summary || "")}</div>
                    ) : null}
                    {typeof operator.auto_resume_eligible === "boolean" ? (
                      <div className="mt-1 text-white/45">
                        Auto resume: {operator.auto_resume_eligible ? "eligible" : "manual only"}
                      </div>
                    ) : null}
                    {Array.isArray(operator.recent_findings) && operator.recent_findings.length ? (
                      <div className="mt-2 rounded-lg border border-white/5 bg-black/20 px-3 py-2">
                        <div className="text-white/40">Recent Findings</div>
                        <div className="mt-2 space-y-2 text-[11px] text-white/65">
                          {operator.recent_findings.slice(0, 3).map((finding: Record<string, any>) => (
                            <div key={String(finding.finding_id || finding.summary || Math.random())}>
                              <div className="text-white/80">
                                {formatReason(String(finding.finding_kind || "finding"))}
                                {finding.strategy_family ? ` • ${formatReason(String(finding.strategy_family || ""))}` : ""}
                              </div>
                              <div className="mt-1">{String(finding.summary || "")}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {Array.isArray(operator.recent_outcomes) && operator.recent_outcomes.length ? (
                      <div className="mt-2 rounded-lg border border-white/5 bg-black/20 px-3 py-2">
                        <div className="text-white/40">Recent Outcomes</div>
                        <div className="mt-2 space-y-2 text-[11px] text-white/65">
                          {operator.recent_outcomes.slice(0, 3).map((outcome: Record<string, any>) => (
                            <div key={String(outcome.outcome_id || outcome.handoff_summary || Math.random())}>
                              <div className="text-white/80">
                                {formatReason(String(outcome.closure_state || "outcome"))}
                                {outcome.recommended_next_action ? ` • ${formatReason(String(outcome.recommended_next_action || ""))}` : ""}
                              </div>
                              <div className="mt-1">{String(outcome.handoff_summary || "")}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {Array.isArray(operator.recent_memories) && operator.recent_memories.length ? (
                      <div className="mt-2 rounded-lg border border-white/5 bg-black/20 px-3 py-2">
                        <div className="text-white/40">Recent Memories</div>
                        <div className="mt-2 space-y-2 text-[11px] text-white/65">
                          {operator.recent_memories.slice(0, 3).map((memory: Record<string, any>) => (
                            <div key={String(memory.memory_id || memory.summary || Math.random())}>
                              <div className="text-white/80">
                                {formatReason(String(memory.memory_kind || "memory"))}
                                {memory.salience ? ` • ${String(memory.salience)}` : ""}
                              </div>
                              <div className="mt-1">{String(memory.summary || "")}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {operator.current_question?.prompt_text ? (
                      <div className="mt-2 rounded-lg border border-amber-400/20 bg-amber-400/10 px-3 py-2">
                        <div className="text-white/40">Current Question</div>
                        <div className="mt-1 text-white/85">{String(operator.current_question.prompt_text || "")}</div>
                        {Array.isArray(operator.current_question.choices) && operator.current_question.choices.length ? (
                          <div className="mt-2 flex flex-wrap gap-2">
                            {operator.current_question.choices.map((choice: string) => (
                              <button
                                key={choice}
                                type="button"
                                onClick={() =>
                                  handleOperatorWorkflowAnswer(String(currentWorkflow.workflow_id || ""), { selected_choice: String(choice || "") })
                                }
                                disabled={
                                  !currentWorkflow.workflow_id ||
                                  busyAction === `operator-workflow:answer:${String(currentWorkflow.workflow_id || "")}`
                                }
                                className="rounded-lg border border-amber-300/20 bg-black/20 px-2 py-1 text-white/75 transition-colors hover:bg-black/30 disabled:opacity-50"
                              >
                                {choice}
                              </button>
                            ))}
                          </div>
                        ) : null}
                        <div className="mt-2 flex flex-wrap gap-2">
                          <input
                            type="text"
                            value={workflowAnswerDrafts[String(currentWorkflow.workflow_id || "")] || ""}
                            onChange={(event) =>
                              setWorkflowAnswerDrafts((current) => ({
                                ...current,
                                [String(currentWorkflow.workflow_id || "")]: event.target.value,
                              }))
                            }
                            placeholder="Answer this workflow question"
                            className="min-w-[220px] flex-1 rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-white placeholder:text-white/30"
                          />
                          <button
                            type="button"
                            onClick={() =>
                              handleOperatorWorkflowAnswer(String(currentWorkflow.workflow_id || ""), {
                                answer_text: workflowAnswerDrafts[String(currentWorkflow.workflow_id || "")] || "",
                              })
                            }
                            disabled={
                              !currentWorkflow.workflow_id ||
                              !String(workflowAnswerDrafts[String(currentWorkflow.workflow_id || "")] || "").trim() ||
                              busyAction === `operator-workflow:answer:${String(currentWorkflow.workflow_id || "")}`
                            }
                            className="rounded-lg border border-amber-300/20 bg-amber-300/10 px-2 py-1 text-amber-100 transition-colors hover:bg-amber-300/15 disabled:opacity-50"
                          >
                            Answer
                          </button>
                        </div>
                      </div>
                    ) : null}
                    {currentWorkflow.metadata?.latest_checkpoint_summary ? (
                      <div className="mt-2 text-white/60">{String(currentWorkflow.metadata.latest_checkpoint_summary)}</div>
                    ) : null}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => handleOperatorWorkflow(String(currentWorkflow.workflow_id || ""), "resume")}
                        disabled={
                          !currentWorkflow.workflow_id ||
                          busyAction === `operator-workflow:resume:${String(currentWorkflow.workflow_id || "")}` ||
                          String(currentWorkflow.wait_state || "") === "confirmation"
                        }
                        className="rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-2 py-1 text-cyan-100 transition-colors hover:bg-cyan-400/15 disabled:opacity-50"
                      >
                        Resume
                      </button>
                      <button
                        type="button"
                        onClick={() => handleOperatorWorkflow(String(currentWorkflow.workflow_id || ""), "pause")}
                        disabled={!currentWorkflow.workflow_id || busyAction === `operator-workflow:pause:${String(currentWorkflow.workflow_id || "")}`}
                        className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                      >
                        Pause
                      </button>
                      <button
                        type="button"
                        onClick={() => handleOperatorWorkflow(String(currentWorkflow.workflow_id || ""), "retry-step")}
                        disabled={!currentWorkflow.workflow_id || busyAction === `operator-workflow:retry-step:${String(currentWorkflow.workflow_id || "")}`}
                        className="rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-2 py-1 text-cyan-100 transition-colors hover:bg-cyan-400/15 disabled:opacity-50"
                      >
                        Retry Step
                      </button>
                      <button
                        type="button"
                        onClick={() => handleOperatorWorkflow(String(currentWorkflow.workflow_id || ""), "new-attempt")}
                        disabled={!currentWorkflow.workflow_id || busyAction === `operator-workflow:new-attempt:${String(currentWorkflow.workflow_id || "")}`}
                        className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                      >
                        New Attempt
                      </button>
                      <button
                        type="button"
                        onClick={() => handleOperatorWorkflow(String(currentWorkflow.workflow_id || ""), "cancel")}
                        disabled={!currentWorkflow.workflow_id || busyAction === `operator-workflow:cancel:${String(currentWorkflow.workflow_id || "")}`}
                        className="rounded-lg border border-red-400/20 bg-red-400/10 px-2 py-1 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : null}
                <div className="mt-3 grid grid-cols-1 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Current Workspace</div>
                    <div className="mt-1 text-white/90">
                      {String(operator.state.current_workspace_label || operator.state.current_workspace_root || "Not set")}
                    </div>
                    {operator.state.current_workspace_root ? (
                      <>
                        <div className="mt-1 break-all text-white/45">{String(operator.state.current_workspace_root || "")}</div>
                        <div className="mt-2 flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() =>
                              handleOperatorWorkspace("pinned", "set", {
                                workspace_root: String(operator.state.current_workspace_root || ""),
                                workspace_label: String(operator.state.current_workspace_label || ""),
                                remote_name: String(operator.state.current_remote_name || ""),
                              })
                            }
                            disabled={busyAction === `operator-workspace:pinned:set:${String(operator.state.current_workspace_root || "")}`}
                            className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                          >
                            Pin Current
                          </button>
                          <button
                            type="button"
                            onClick={() => handleOperatorWorkspace("active", "clear")}
                            disabled={busyAction === "operator-workspace:active:clear:none"}
                            className="rounded-lg border border-red-400/20 bg-red-400/10 px-2 py-1 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                          >
                            Clear Current
                          </button>
                        </div>
                      </>
                    ) : null}
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Pinned Workspace</div>
                    <div className="mt-1 text-white/90">
                      {String(operator.state.pinned_workspace_label || operator.state.pinned_workspace_root || "Not set")}
                    </div>
                    {operator.state.pinned_workspace_root ? (
                      <>
                        <div className="mt-1 break-all text-white/45">{String(operator.state.pinned_workspace_root || "")}</div>
                        <div className="mt-2 flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() =>
                              handleOperatorWorkspace("active", "set", {
                                workspace_root: String(operator.state.pinned_workspace_root || ""),
                                workspace_label: String(operator.state.pinned_workspace_label || ""),
                                remote_name: String(operator.state.pinned_remote_name || ""),
                              })
                            }
                            disabled={busyAction === `operator-workspace:active:set:${String(operator.state.pinned_workspace_root || "")}`}
                            className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                          >
                            Use as Current
                          </button>
                          <button
                            type="button"
                            onClick={() => handleOperatorWorkspace("pinned", "clear")}
                            disabled={busyAction === "operator-workspace:pinned:clear:none"}
                            className="rounded-lg border border-red-400/20 bg-red-400/10 px-2 py-1 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                          >
                            Clear Pinned
                          </button>
                        </div>
                      </>
                    ) : null}
                  </div>
                  {(operator.recent_workspaces || []).length ? (
                    <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                      <div className="text-white/40">Recent Workspaces</div>
                      <div className="mt-2 space-y-2">
                        {(operator.recent_workspaces || []).slice(0, 4).map((workspace) => {
                          const workspaceRoot = String(workspace.workspace_root || "");
                          const workspaceLabel = String(workspace.workspace_label || workspaceRoot || "Workspace");
                          return (
                            <div key={workspaceRoot} className="rounded-lg border border-white/5 bg-white/[0.03] px-2 py-2">
                              <div className="truncate text-white/85">{workspaceLabel}</div>
                              <div className="mt-1 break-all text-[11px] text-white/45">{workspaceRoot}</div>
                              <div className="mt-2 flex flex-wrap gap-2">
                                <button
                                  type="button"
                                  onClick={() =>
                                    handleOperatorWorkspace("active", "set", {
                                      workspace_root: workspaceRoot,
                                      workspace_label: workspaceLabel,
                                      remote_name: String(workspace.target_remote_name || ""),
                                    })
                                  }
                                  disabled={busyAction === `operator-workspace:active:set:${workspaceRoot}`}
                                  className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                                >
                                  Use
                                </button>
                                <button
                                  type="button"
                                  onClick={() =>
                                    handleOperatorWorkspace("pinned", "set", {
                                      workspace_root: workspaceRoot,
                                      workspace_label: workspaceLabel,
                                      remote_name: String(workspace.target_remote_name || ""),
                                    })
                                  }
                                  disabled={busyAction === `operator-workspace:pinned:set:${workspaceRoot}`}
                                  className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                                >
                                  Pin
                                </button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Browser Session</div>
                    <div className="mt-1 text-white/90">
                      {String(operator.machine_context?.active_browser_session?.title || operator.machine_context?.active_browser_session?.url || "Idle")}
                    </div>
                    {operator.machine_context?.active_browser_session?.url ? (
                      <div className="mt-1 break-all text-white/45">
                        {String(operator.machine_context.active_browser_session.url || "")}
                      </div>
                    ) : null}
                    <div className="mt-2 text-white/45">
                      {operator.machine_context?.active_browser_session?.status
                        ? formatReason(String(operator.machine_context.active_browser_session.status || "ready"))
                        : "No live browser session"}
                      {operator.machine_context?.active_browser_session?.tab_count
                        ? ` • ${String(operator.machine_context.active_browser_session.tab_count)} tabs`
                        : ""}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => handleOperatorBrowser("focus")}
                        disabled={busyAction === "operator-browser:focus"}
                        className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                      >
                        Focus Browser
                      </button>
                      <button
                        type="button"
                        onClick={() => handleOperatorBrowser("reset")}
                        disabled={busyAction === "operator-browser:reset"}
                        className="rounded-lg border border-red-400/20 bg-red-400/10 px-2 py-1 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                      >
                        Reset Session
                      </button>
                    </div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Host Backend</div>
                    <div className="mt-1 text-white/90">
                      {String(
                        operator.host_platform?.description ||
                          operator.host_platform?.host_backend ||
                          operator.host_platform?.host_platform ||
                          "Unknown",
                      )}
                    </div>
                    <div className="mt-1 text-white/45">
                      {String(operator.host_platform?.host_platform || "unknown")}
                      {operator.host_platform?.display_server ? ` • ${String(operator.host_platform.display_server)}` : ""}
                    </div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Focused Window</div>
                    <div className="mt-1 text-white/90">
                      {String(operator.machine_context?.focused_window?.title || operator.machine_context?.focused_window?.wm_class || "Unknown")}
                    </div>
                    {operator.machine_context?.focused_window?.window_id ? (
                      <div className="mt-1 text-white/45">
                        {String(operator.machine_context.focused_window.wm_class || "")}
                        {operator.machine_context.focused_window.wm_class ? " • " : ""}
                        {String(operator.machine_context.focused_window.window_id || "")}
                      </div>
                    ) : (
                      <div className="mt-1 text-white/45">No focused window snapshot</div>
                    )}
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Desktop Inspection</div>
                    <div className="mt-1 text-white/90">
                      {String(
                        operator.last_inspection?.summary ||
                          operator.desktop_perception?.last_inspection?.summary ||
                          "No recent inspection",
                      )}
                    </div>
                    {operator.last_inspection?.created_at || operator.desktop_perception?.last_inspection?.created_at ? (
                      <div className="mt-1 text-white/45">
                        {formatTimestamp(
                          Number(operator.last_inspection?.created_at || operator.desktop_perception?.last_inspection?.created_at || 0),
                        )}
                      </div>
                    ) : null}
                    {operator.last_resolved_target?.label || operator.desktop_perception?.last_resolved_target?.label ? (
                      <div className="mt-2 text-white/55">
                        Target:{" "}
                        {String(
                          operator.last_resolved_target?.label ||
                            operator.desktop_perception?.last_resolved_target?.label ||
                            "",
                        )}
                      </div>
                    ) : null}
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={handleOperatorDesktopInspect}
                        disabled={busyAction === "operator-desktop:inspect"}
                        className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                      >
                        Inspect Focused Window
                      </button>
                    </div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Surface Reliability</div>
                    <div className="mt-1 text-white/90">
                      Recoveries {Number(operator.surface_reliability?.counts?.recovery_attempts || 0)}
                    </div>
                    <div className="mt-1 text-white/45">
                      Fallbacks {Number(operator.surface_reliability?.counts?.fallback_events || 0)} • Degraded {Number(operator.surface_reliability?.counts?.degraded_events || 0)}
                    </div>
                    {operator.focused_app_adapter?.label ? (
                      <div className="mt-2 text-white/55">
                        Focused app: {String(operator.focused_app_adapter.label || "")}
                      </div>
                    ) : null}
                    {(operator.surface_reliability?.recent_events || []).length > 0 ? (
                      <div className="mt-2 space-y-1.5 text-xs">
                        {(operator.surface_reliability?.recent_events || []).slice(0, 3).map((item, index) => (
                          <div key={String(item.timestamp || index)} className="rounded-lg border border-white/5 bg-black/20 px-2 py-2">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-white/75">
                                {formatReason(String(item.surface || "surface"))} • {formatReason(String(item.tool_name || "action"))}
                              </span>
                              <span className={statusTone(String(item.status || "unknown"))}>{String(item.status || "unknown")}</span>
                            </div>
                            <div className="mt-1 text-white/45">
                              {String(item.final_reason || item.failure_kind || "recovered")}
                              {item.fallback_used ? ` • fallback ${String(item.fallback_target || "")}` : ""}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="mt-2 text-white/45">No recent recoveries or degraded actions</div>
                    )}
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">App Adapters</div>
                    <div className="mt-2 space-y-2">
                      {(operator.app_adapters || []).length > 0 ? (
                        (operator.app_adapters || []).slice(0, 4).map((adapter) => {
                          const appId = String(adapter.app_id || "");
                          return (
                            <div key={appId} className="rounded-lg border border-white/5 bg-black/20 px-2 py-2">
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-white/75">
                                  {String(adapter.label || appId)}
                                  {adapter.is_focused ? " • focused" : ""}
                                </span>
                                <span className={statusTone(String(adapter.health?.status || "degraded"))}>
                                  {String(adapter.health?.status || "degraded")}
                                </span>
                              </div>
                              <div className="mt-1 text-white/45">{String(adapter.health?.message || "")}</div>
                              <div className="mt-1 text-white/40">
                                {(adapter.capabilities || []).map((item) => formatReason(String(item || ""))).join(" • ")}
                              </div>
                              <div className="mt-2 flex flex-wrap gap-2">
                                <button
                                  type="button"
                                  onClick={() => handleOperatorAppFocus(appId)}
                                  disabled={busyAction === `operator-app:focus:${appId}`}
                                  className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                                >
                                  Focus
                                </button>
                              </div>
                            </div>
                          );
                        })
                      ) : (
                        <div className="text-white/45">No app adapters reported</div>
                      )}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => handleOperatorAppFocus("vscode")}
                        disabled={busyAction === "operator-app:focus:vscode"}
                        className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                      >
                        Focus VS Code
                      </button>
                      <button
                        type="button"
                        onClick={() => handleOperatorAppFocus("file_manager")}
                        disabled={busyAction === "operator-app:focus:file_manager"}
                        className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                      >
                        Focus File Manager
                      </button>
                    </div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Host + Adapter Health</div>
                    <div className="mt-2 space-y-2">
                      {Object.entries({
                        ...(operator.host_capability_health || {}),
                        ...(operator.adapter_health || {}),
                      }).length > 0 ? (
                        Object.entries({
                          ...(operator.host_capability_health || {}),
                          ...(operator.adapter_health || {}),
                        }).map(([key, item]) => (
                          <div key={key} className="rounded-lg border border-white/5 bg-black/20 px-2 py-1.5">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-white/70">{formatReason(key)}</span>
                              <span className={statusTone(String(item?.status || "ready"))}>{String(item?.status || "ready")}</span>
                            </div>
                            <div className="mt-1 text-white/45">{String(item?.message || "")}</div>
                          </div>
                        ))
                      ) : (
                        <div className="text-white/45">No host or adapter health reported</div>
                      )}
                    </div>
                  </div>
                </div>
                {operator.review_summary ? (
                  <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] p-3 text-xs">
                    <div className="text-white/40">Review Summary</div>
                    <div className="mt-1 text-white/80">{String(operator.review_summary || "")}</div>
                    {operator.last_review_nudge?.message ? (
                      <div className="mt-2 text-white/50">
                        Last nudge: {String(operator.last_review_nudge.message || "")}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {Array.isArray(operator.review_queue) && operator.review_queue.length ? (
                  <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] p-3 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-white/40">Review Queue</div>
                      <div className="text-white/45">
                        {operator.next_review_due?.next_review_at
                          ? `Next due ${formatTimestamp(Number(operator.next_review_due.next_review_at || 0))}`
                          : "No review due"}
                      </div>
                    </div>
                    <div className="mt-2 space-y-2">
                      {(operator.review_queue || []).slice(0, 4).map((item: Record<string, any>) => {
                        const followthroughId = String(item.followthrough_id || "");
                        const auditNotice = buildWorkflowAuditNotice(item);
                        const reviewReason = String(item.review_reason || "").trim();
                        const nextMoveText = String(item.next_move_text || "").trim();
                        return (
                          <div key={followthroughId} className="rounded-lg border border-white/5 bg-black/20 px-3 py-2">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-white/80">
                                {formatReason(String(item.review_kind || item.status || "open"))}
                                {item.review_priority ? ` • ${String(item.review_priority)}` : ""}
                                {item.salience ? ` • ${String(item.salience)}` : ""}
                              </span>
                              <span className="text-white/40">
                                {item.next_review_at ? formatTimestamp(Number(item.next_review_at || 0)) : "no review time"}
                              </span>
                            </div>
                            <div className="mt-1 text-white/85">{String(item.summary || "")}</div>
                            {reviewReason && reviewReason !== auditNotice?.detail ? (
                              <div className="mt-1 text-white/55">Why now: {reviewReason}</div>
                            ) : null}
                            <WorkflowAuditCallout notice={auditNotice} className="mt-2" />
                            {nextMoveText && nextMoveText !== auditNotice?.nextMove ? (
                              <div className="mt-1 text-white/45">
                                Next move: {nextMoveText}
                              </div>
                            ) : null}
                            <div className="mt-2 flex flex-wrap gap-2">
                              <button
                                type="button"
                                onClick={() => handleOperatorFollowthrough(followthroughId, "snooze", 30)}
                                disabled={!followthroughId || busyAction === `operator-followthrough:snooze:${followthroughId}`}
                                className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                              >
                                Snooze 30m
                              </button>
                              <button
                                type="button"
                                onClick={() => handleOperatorFollowthrough(followthroughId, "snooze", 360)}
                                disabled={!followthroughId || busyAction === `operator-followthrough:snooze:${followthroughId}`}
                                className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                              >
                                Snooze 6h
                              </button>
                              <button
                                type="button"
                                onClick={() => handleOperatorFollowthrough(followthroughId, "resolve")}
                                disabled={!followthroughId || busyAction === `operator-followthrough:resolve:${followthroughId}`}
                                className="rounded-lg border border-emerald-400/20 bg-emerald-400/10 px-2 py-1 text-emerald-100 transition-colors hover:bg-emerald-400/15 disabled:opacity-50"
                              >
                                Resolve
                              </button>
                              <button
                                type="button"
                                onClick={() => handleOperatorFollowthrough(followthroughId, "dismiss")}
                                disabled={!followthroughId || busyAction === `operator-followthrough:dismiss:${followthroughId}`}
                                className="rounded-lg border border-red-400/20 bg-red-400/10 px-2 py-1 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                              >
                                Dismiss
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
                {Array.isArray(operator.recent_wins) && operator.recent_wins.length ? (
                  <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] p-3 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-white/40">Recent Wins</div>
                      <div className="text-white/45">{operator.review_counts?.recent_wins || operator.recent_wins.length}</div>
                    </div>
                    <div className="mt-2 space-y-2">
                      {(operator.recent_wins || []).slice(0, 3).map((item: Record<string, any>) => (
                        <div key={String(item.memory_id || item.workflow_id || Math.random())} className="rounded-lg border border-white/5 bg-black/20 px-3 py-2">
                          <div className="text-white/85">{String(item.summary || "")}</div>
                          {item.carry_forward_text ? (
                            <div className="mt-1 text-white/50">Carry forward: {String(item.carry_forward_text || "")}</div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                {Array.isArray(memoryPreview?.recent_captures) && memoryPreview.recent_captures.length ? (
                  <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] p-3 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-white/40">Memory Workspace Preview</div>
                      <Link href="/account/memory" className="text-white/55 transition-colors hover:text-white">
                        Open memory workspace
                      </Link>
                    </div>
                    <div className="mt-2 space-y-2">
                      {memoryPreview.recent_captures.slice(0, 3).map((item) => (
                        <div key={String(item.item_id || Math.random())} className="rounded-lg border border-white/5 bg-black/20 px-3 py-2">
                          <div className="text-white/45">{String(item.source_kind || "memory")}</div>
                          <div className="mt-1 text-white/80">{String(item.snippet || "")}</div>
                        </div>
                      ))}
                    </div>
                    <div className="mt-2 text-white/45">
                      Mission control only mirrors the newest captures. Recall mode: {String(memoryPreview.capability?.mode || "lexical_only")}
                    </div>
                  </div>
                ) : null}
                <div className="mt-3 space-y-2 text-xs">
                  {(operator.recent_workflows || []).slice(0, 5).map((workflow) => {
                    const workflowId = String(workflow.workflow_id || "");
                    const workflowStatus = String(workflow.status || "active");
                    const waitState = String(workflow.wait_state || "none");
                    const auditNotice = buildWorkflowAuditNotice(workflow);
                    return (
                      <div key={workflowId} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-white/85">{String(workflow.objective || workflowId)}</span>
                          <span className={statusTone(workflowStatus)}>{workflowStatus}</span>
                        </div>
                        <div className="mt-1 text-white/45">
                          {formatReason(String(workflow.current_phase || "executing"))}
                          {waitState && waitState !== "none" ? ` • ${formatReason(waitState)}` : ""}
                        </div>
                        {workflow.strategy_family ? (
                          <div className="mt-1 text-white/50">
                            Strategy: {formatReason(String(workflow.strategy_family || ""))}
                            {workflow.strategy_exhausted ? " • exhausted" : ""}
                          </div>
                        ) : null}
                        {workflow.closure_state ? (
                          <div className="mt-1 text-white/50">Closure: {formatReason(String(workflow.closure_state || ""))}</div>
                        ) : null}
                        {workflow.blocker_class ? (
                          <div className="mt-1 text-white/50">Blocker: {formatReason(String(workflow.blocker_class || ""))}</div>
                        ) : null}
                        <WorkflowAuditCallout notice={auditNotice} className="mt-2" />
                        {workflow.latest_handoff_summary ? (
                          <div className="mt-2 text-white/60">{String(workflow.latest_handoff_summary || "")}</div>
                        ) : workflow.metadata?.latest_checkpoint_summary ? (
                          <div className="mt-2 text-white/60">{String(workflow.metadata.latest_checkpoint_summary)}</div>
                        ) : null}
                        <div className="mt-2 flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() => handleOperatorWorkflow(workflowId, "resume")}
                            disabled={!workflowId || waitState === "confirmation" || busyAction === `operator-workflow:resume:${workflowId}`}
                            className="rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-2 py-1 text-cyan-100 transition-colors hover:bg-cyan-400/15 disabled:opacity-50"
                          >
                            Resume
                          </button>
                          <button
                            type="button"
                            onClick={() => handleOperatorWorkflow(workflowId, "pause")}
                            disabled={!workflowId || busyAction === `operator-workflow:pause:${workflowId}`}
                            className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                          >
                            Pause
                          </button>
                          <button
                            type="button"
                            onClick={() => handleOperatorWorkflow(workflowId, "retry-step")}
                            disabled={!workflowId || busyAction === `operator-workflow:retry-step:${workflowId}`}
                            className="rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-2 py-1 text-cyan-100 transition-colors hover:bg-cyan-400/15 disabled:opacity-50"
                          >
                            Retry Step
                          </button>
                          <button
                            type="button"
                            onClick={() => handleOperatorWorkflow(workflowId, "new-attempt")}
                            disabled={!workflowId || busyAction === `operator-workflow:new-attempt:${workflowId}`}
                            className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                          >
                            New Attempt
                          </button>
                          <button
                            type="button"
                            onClick={() => handleOperatorWorkflow(workflowId, "cancel")}
                            disabled={!workflowId || busyAction === `operator-workflow:cancel:${workflowId}`}
                            className="rounded-lg border border-red-400/20 bg-red-400/10 px-2 py-1 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="mt-3 space-y-2 text-xs">
                  {(operator.recent_actions || []).slice(0, 5).map((action) => {
                    const actionId = String(action.action_id || "");
                    const workspaceRoot = String(action.workspace_root || "");
                    const status = String(action.status || "queued");
                    const canCancel = ["planned", "queued", "running", "waiting_confirmation"].includes(status);
                    const canRetry = ["failed", "blocked", "cancelled"].includes(status);
                    return (
                      <div key={actionId} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-white/85">{String(action.workspace_label || action.intent_kind || "operator_action")}</span>
                          <span className={statusTone(status)}>{status}</span>
                        </div>
                        <div className="mt-1 break-words text-white/70">{String(action.objective || actionId)}</div>
                        <div className="mt-1 text-white/45">
                          {formatReason(String(action.trigger_source || ""))}
                          {action.target_remote_name ? ` • ${String(action.target_remote_name)}` : ""}
                          {action.managed_branch_name ? ` • ${String(action.managed_branch_name)}` : ""}
                        </div>
                        {workspaceRoot ? <div className="mt-1 break-all text-white/40">{workspaceRoot}</div> : null}
                        {action.result_summary ? <div className="mt-2 text-white/55">{String(action.result_summary)}</div> : null}
                        <div className="mt-2 flex flex-wrap gap-2">
                          {workspaceRoot ? (
                            <>
                              <button
                                type="button"
                                onClick={() =>
                                  handleOperatorWorkspace("active", "set", {
                                    workspace_root: workspaceRoot,
                                    workspace_label: String(action.workspace_label || ""),
                                    remote_name: String(action.target_remote_name || ""),
                                  })
                                }
                                disabled={busyAction === `operator-workspace:active:set:${workspaceRoot}`}
                                className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                              >
                                Use Current
                              </button>
                              <button
                                type="button"
                                onClick={() =>
                                  handleOperatorWorkspace("pinned", "set", {
                                    workspace_root: workspaceRoot,
                                    workspace_label: String(action.workspace_label || ""),
                                    remote_name: String(action.target_remote_name || ""),
                                  })
                                }
                                disabled={busyAction === `operator-workspace:pinned:set:${workspaceRoot}`}
                                className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                              >
                                Pin
                              </button>
                            </>
                          ) : null}
                          {canRetry ? (
                            <button
                              type="button"
                              onClick={() => handleOperatorAction(actionId, "retry")}
                              disabled={busyAction === `operator-action:retry:${actionId}`}
                              className="rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-2 py-1 text-cyan-100 transition-colors hover:bg-cyan-400/15 disabled:opacity-50"
                            >
                              Retry
                            </button>
                          ) : null}
                          {canCancel ? (
                            <button
                              type="button"
                              onClick={() => handleOperatorAction(actionId, "cancel")}
                              disabled={busyAction === `operator-action:cancel:${actionId}`}
                              className="rounded-lg border border-red-400/20 bg-red-400/10 px-2 py-1 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                            >
                              Cancel
                            </button>
                          ) : null}
                        </div>
                      </div>
                    );
                  })}
                  {(operator.pending_confirmations || []).slice(0, 3).map((confirmation) => (
                    <div key={String(confirmation.token || "")} className="rounded-xl border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-xs text-amber-100">
                      Pending approval {String(confirmation.token || "")}
                      {confirmation.command ? ` • ${String(confirmation.command)}` : ""}
                    </div>
                  ))}
                </div>
              </SectionCard>
            ) : null}

            {reminders ? (
              <SectionCard icon={<Clock3 className="h-4 w-4" />} title="Reminders" status={reminders.status}>
                <div className="mb-3 flex items-center justify-between gap-3 text-xs">
                  <div className="text-white/45">
                    Last check {formatTimestamp(reminders.loop.last_check_completed_at)} · {reminders.loop.loop_state}
                  </div>
                  <button
                    type="button"
                    onClick={handleReminderCheckNow}
                    disabled={busyAction === "reminders-check-now"}
                    className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-white/80 transition-colors hover:bg-white/10 disabled:opacity-50"
                  >
                    {busyAction === "reminders-check-now" ? "Checking..." : "Check Now"}
                  </button>
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Due</div>
                    <div className="mt-1 text-white/90">{reminders.counts.due}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Pending</div>
                    <div className="mt-1 text-white/90">{reminders.counts.pending}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Total</div>
                    <div className="mt-1 text-white/90">{reminders.counts.total}</div>
                  </div>
                </div>
                {reminders.latest_dispatch ? (
                  <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2 text-white/45">
                      <span>Latest delivery</span>
                      <span>{formatRelative(reminders.latest_dispatch.activity_at)}</span>
                    </div>
                    <div className="mt-1 text-white/85">{reminders.latest_dispatch.summary}</div>
                  </div>
                ) : null}
                {(reminders.recent_loop_activity || []).length > 0 ? (
                  <div className="mt-3">
                    <div className="mb-2 text-[11px] uppercase tracking-[0.24em] text-white/35">Recent loop activity</div>
                    <div className="space-y-2 text-xs">
                      {(reminders.recent_loop_activity || []).slice(0, 3).map((item, index) => (
                        <div key={`${String(item.kind || "event")}-${String(item.timestamp || index)}`} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-white/85">{String(item.summary || "Reminder activity recorded.")}</span>
                            <span className="text-white/45">{formatRelative(item.timestamp)}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                {(reminders.recent_deliveries || []).length > 0 ? (
                  <div className="mt-3">
                    <div className="mb-2 text-[11px] uppercase tracking-[0.24em] text-white/35">Delivery history</div>
                    <div className="space-y-2 text-xs">
                      {(reminders.recent_deliveries || []).slice(0, 3).map((delivery, index) => (
                        <div key={`${String(delivery.conversation_id || "delivery")}-${index}`} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-white/85">{String(delivery.summary || "Reminder delivered.")}</span>
                            <span className="text-white/45">{formatRelative(delivery.delivered_at ?? delivery.timestamp)}</span>
                          </div>
                          <div className="mt-1 text-white/45">
                            {String(delivery.delivery_outcome_summary || "Delivered.")}
                            {delivery.scheduled_time ? ` · Due ${formatRelative(delivery.scheduled_time)}` : ""}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                <div className="mt-3 space-y-2 text-xs">
                  {[...(reminders.due_reminders || []).slice(0, 2), ...(reminders.pending_reminders || []).slice(0, 3)].map((reminder) => {
                    const deliveryIntentSummary = String(reminder.delivery_intent_summary || "").trim();
                    const continuityContextSummary = String(reminder.continuity_context_summary || "").trim();
                    return (
                      <div key={reminder.id} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-white/85">{reminder.reminder_text}</span>
                          <span className="text-white/45">{formatRelative(reminder.scheduled_time)}</span>
                        </div>
                        {deliveryIntentSummary ? (
                          <div className="mt-1 text-white/55">{deliveryIntentSummary}</div>
                        ) : null}
                        {continuityContextSummary ? (
                          <div className="mt-1 text-white/45">{continuityContextSummary}</div>
                        ) : null}
                        <div className="mt-2 flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => handleReminderSnooze(Number(reminder.id))}
                            disabled={busyAction === `reminder-snooze:${reminder.id}`}
                            className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                          >
                            Snooze 15m
                          </button>
                          <button
                            type="button"
                            onClick={() => handleReminderCancel(Number(reminder.id))}
                            disabled={busyAction === `reminder-cancel:${reminder.id}`}
                            className="rounded-lg border border-red-400/20 bg-red-400/10 px-2 py-1 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </SectionCard>
            ) : null}

            {heartbeat ? (
              <SectionCard icon={<Sparkles className="h-4 w-4" />} title="Heartbeat" status={heartbeat.status}>
                <div className="mb-3 grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Loop State</div>
                    <div className="mt-1 text-white/90">{heartbeat.loop.loop_state}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Last Check</div>
                    <div className="mt-1 text-white/90">{formatTimestamp(heartbeat.loop.last_check_completed_at)}</div>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2 text-xs">
                  <button
                    type="button"
                    onClick={() => handleHeartbeatAction("check-now")}
                    disabled={!heartbeat.controls.can_check_now || busyAction === "heartbeat-check-now"}
                    className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-white/80 transition-colors hover:bg-white/10 disabled:opacity-50"
                  >
                    {busyAction === "heartbeat-check-now" ? "Checking..." : "Check Now"}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleHeartbeatAction("pause")}
                    disabled={!heartbeat.controls.can_pause || busyAction === "heartbeat-pause"}
                    className="rounded-xl border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-amber-100 transition-colors hover:bg-amber-400/15 disabled:opacity-50"
                  >
                    <span className="inline-flex items-center gap-1">
                      <PauseCircle className="h-3.5 w-3.5" />
                      Pause
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleHeartbeatAction("resume")}
                    disabled={!heartbeat.controls.can_resume || busyAction === "heartbeat-resume"}
                    className="rounded-xl border border-emerald-400/20 bg-emerald-400/10 px-3 py-2 text-emerald-100 transition-colors hover:bg-emerald-400/15 disabled:opacity-50"
                  >
                    <span className="inline-flex items-center gap-1">
                      <PlayCircle className="h-3.5 w-3.5" />
                      Resume
                    </span>
                  </button>
                </div>
                <div className="mt-3 space-y-2 text-xs">
                  {heartbeat.loop.last_decision ? (
                    <div className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2 text-white/70">
                      Decision: {String(heartbeat.loop.last_decision.reason || heartbeat.loop.last_decision.message_type || "n/a")}
                    </div>
                  ) : null}
                  {heartbeat.last_run && !(heartbeat.recent_runs || []).length ? (
                    <div className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2 text-white/70">
                      Last run: {String(heartbeat.last_run.summary || heartbeat.last_run.why_now || "n/a")}
                    </div>
                  ) : null}
                  {heartbeat.loop.last_action ? (
                    <div className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2 text-white/70">
                      Last action: {String(heartbeat.loop.last_action.status || "unknown")}
                    </div>
                  ) : null}
                  {(heartbeat.recent_runs || []).slice(0, 3).map((run, index) => {
                    const runLabel = String(
                      run.decision_kind_label || formatReason(String(run.decision_kind || "heartbeat")),
                    ).trim();
                    const runSummary = String(run.summary || "").trim();
                    const whyNow = String(run.why_now || "").trim();
                    const runMeta = [
                      String(run.status_label || formatReason(String(run.status || ""))).trim(),
                      String(run.surface_kind_label || formatReason(String(run.surface_kind || ""))).trim(),
                    ].filter(Boolean).join(" • ");
                    return (
                      <div key={`${run.heartbeat_run_id || index}`} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-white/85">{runLabel || "heartbeat run"}</span>
                          <span className="text-white/45">{formatRelative(run.completed_at || run.updated_at || run.started_at)}</span>
                        </div>
                        {runSummary ? <div className="mt-1 text-white/80">{runSummary}</div> : null}
                        {whyNow && whyNow !== runSummary ? <div className="mt-1 text-white/60">Why now: {whyNow}</div> : null}
                        {runMeta ? <div className="mt-1 text-white/45">{runMeta}</div> : null}
                      </div>
                    );
                  })}
                  {(heartbeat.recent_actions || []).slice(0, 3).map((action, index) => (
                    (() => {
                      const actionLabel = String(
                        action.action_type_label || formatReason(String(action.action_type || "proactive_message")),
                      ).trim();
                      const contentPreview = String(action.content_preview || "").trim();
                      const whyNow = String(action.why_now || "").trim();
                      const deliveryOutcome = String(action.delivery_outcome_summary || "").trim();
                      const fallbackSummary = String(action.summary || "").trim();
                      const bodyText = contentPreview || (fallbackSummary && fallbackSummary !== whyNow ? fallbackSummary : "");
                      return (
                        <div key={`${action.id || index}`} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-white/85">{actionLabel || "proactive message"}</span>
                            <span className="text-white/45">{formatRelative(action.executed_time || action.scheduled_time)}</span>
                          </div>
                          {bodyText ? <div className="mt-1 text-white/80">{bodyText}</div> : null}
                          {whyNow ? <div className="mt-1 text-white/60">Why now: {whyNow}</div> : null}
                          {deliveryOutcome ? <div className="mt-1 text-white/45">{deliveryOutcome}</div> : null}
                        </div>
                      );
                    })()
                  ))}
                </div>
              </SectionCard>
            ) : null}

            {standingOrders ? (
              <SectionCard icon={<Sparkles className="h-4 w-4" />} title="Standing Orders" status={standingOrders.status}>
                <div className="mb-3 grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Active</div>
                    <div className="mt-1 text-white/90">{standingOrders.counts?.active ?? 0}</div>
                  </div>
                  <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <div className="text-white/40">Due Now</div>
                    <div className="mt-1 text-white/90">{standingOrders.counts?.due ?? 0}</div>
                  </div>
                </div>
                <div className="space-y-2 text-xs">
                  {(standingOrders.active_orders || []).slice(0, 4).map((order) => {
                    const orderId = String(order.standing_order_id || "");
                    return (
                      <div key={orderId} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-white/85">{String(order.title || "Standing order")}</span>
                          <span className="text-white/45">{String(order.capability_status || "review_only")}</span>
                        </div>
                        <div className="mt-1 text-white/55">
                          Next run: {order.next_run_at ? formatRelative(Number(order.next_run_at)) : "heartbeat"}
                        </div>
                        <div className="mt-2 flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() => handleStandingOrderAction(orderId, order.status === "paused" ? "resume" : "pause")}
                            disabled={!orderId || busyAction === `standing-order:${order.status === "paused" ? "resume" : "pause"}:${orderId}`}
                            className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
                          >
                            {order.status === "paused" ? "Resume" : "Pause"}
                          </button>
                          <button
                            type="button"
                            onClick={() => handleStandingOrderAction(orderId, "run-now")}
                            disabled={!orderId || busyAction === `standing-order:run-now:${orderId}`}
                            className="rounded-lg border border-emerald-400/20 bg-emerald-400/10 px-2 py-1 text-emerald-100 transition-colors hover:bg-emerald-400/15 disabled:opacity-50"
                          >
                            Run Now
                          </button>
                          <button
                            type="button"
                            onClick={() => handleStandingOrderAction(orderId, "disable")}
                            disabled={!orderId || busyAction === `standing-order:disable:${orderId}`}
                            className="rounded-lg border border-red-400/20 bg-red-400/10 px-2 py-1 text-red-100 transition-colors hover:bg-red-400/15 disabled:opacity-50"
                          >
                            Disable
                          </button>
                        </div>
                      </div>
                    );
                  })}
                  {(standingOrders.recent_runs || []).slice(0, 4).map((run, index) => {
                    const title = String(run.title || "Standing order").trim();
                    const runSummary = String(run.summary || run.result_summary || "").trim();
                    const whyNow = String(run.why_now || "").trim();
                    const runMeta = [
                      String(run.status_label || formatReason(String(run.status || ""))).trim(),
                      String(run.surface_kind_label || formatReason(String(run.surface_kind || ""))).trim(),
                      String(run.trigger_kind_label || formatReason(String(run.trigger_kind || ""))).trim(),
                    ].filter(Boolean).join(" • ");
                    return (
                      <div key={`${run.standing_order_run_id || index}`} className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-white/85">{title}</span>
                          <span className="text-white/45">{formatRelative(run.updated_at || run.created_at)}</span>
                        </div>
                        {runSummary ? <div className="mt-1 text-white/80">{runSummary}</div> : null}
                        {whyNow && whyNow !== runSummary ? <div className="mt-1 text-white/60">Why now: {whyNow}</div> : null}
                        {runMeta ? <div className="mt-1 text-white/45">{runMeta}</div> : null}
                      </div>
                    );
                  })}
                </div>
              </SectionCard>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}
