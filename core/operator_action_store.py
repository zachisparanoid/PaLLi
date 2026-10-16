from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional


class OperatorActionStore:
    """Persistence helper for operator action records."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    def _clean_text(self, value: Any, *, default: str = "") -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text or default

    def _clean_optional_text(self, value: Any) -> Optional[str]:
        text = self._clean_text(value)
        return text or None

    def _clean_text_list(self, values: Optional[List[Any]]) -> List[str]:
        clean_values: List[str] = []
        for value in values or []:
            text = self._clean_text(value)
            if text:
                clean_values.append(text)
        return clean_values

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _action_record(self, row: Any) -> Dict[str, Any]:
        return {
            "action_id": self._clean_text(row[0]),
            "owner_user_id": int(row[1]),
            "runtime_job_id": self._clean_text(row[2]),
            "agent_run_id": self._clean_text(row[3]),
            "source_runtime_session_key": self._clean_text(row[4]),
            "root_session_key": self._clean_text(row[5]),
            "workflow_id": self._clean_text(row[6]),
            "workflow_step_id": self._clean_text(row[7]),
            "resumed_from_checkpoint_id": self._clean_text(row[8]),
            "resume_launch_mode": self._clean_text(row[9]),
            "workspace_root": self._clean_text(row[10]),
            "workspace_label": self._clean_text(row[11]),
            "target_remote_name": self._clean_text(row[12]),
            "managed_branch_name": self._clean_text(row[13]),
            "trigger_source": self._clean_text(row[14]),
            "intent_kind": self._clean_text(row[15]),
            "status": self._clean_text(row[16]),
            "objective": self._clean_text(row[17]),
            "decision_reason": self._clean_text(row[18]),
            "result_summary": self._clean_text(row[19]),
            "last_transition_reason": self._clean_text(row[20]),
            "retry_of_action_id": self._clean_text(row[21]),
            "replacement_action_id": self._clean_text(row[22]),
            "metadata": self._load_json(row[23], {}),
            "created_at": float(row[24] or 0.0),
            "updated_at": float(row[25] or 0.0),
            "completed_at": float(row[26]) if row[26] is not None else None,
        }

    async def create_operator_action(
        self,
        *,
        action_id: str,
        owner_user_id: int,
        runtime_job_id: Optional[str],
        agent_run_id: Optional[str],
        source_runtime_session_key: Optional[str],
        root_session_key: Optional[str],
        workflow_id: Optional[str],
        workflow_step_id: Optional[str],
        resumed_from_checkpoint_id: Optional[str],
        resume_launch_mode: Optional[str],
        workspace_root: str,
        workspace_label: Optional[str],
        target_remote_name: Optional[str],
        managed_branch_name: Optional[str],
        trigger_source: str,
        intent_kind: str,
        status: str,
        objective: str,
        decision_reason: Optional[str] = None,
        result_summary: Optional[str] = None,
        last_transition_reason: Optional[str] = None,
        retry_of_action_id: Optional[str] = None,
        replacement_action_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        completed_at: Optional[float] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            await conn.execute(
                """
                INSERT INTO operator_actions
                (
                    action_id, owner_user_id, runtime_job_id, agent_run_id, source_runtime_session_key,
                    root_session_key, workflow_id, workflow_step_id, resumed_from_checkpoint_id, resume_launch_mode,
                    workspace_root, workspace_label, target_remote_name, managed_branch_name,
                    trigger_source, intent_kind, status, objective, decision_reason, result_summary,
                    last_transition_reason, retry_of_action_id, replacement_action_id, metadata,
                    created_at, updated_at, completed_at
                )
                VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24::jsonb, $25, $25, $26)
                ON CONFLICT (action_id) DO UPDATE SET
                    runtime_job_id = COALESCE(EXCLUDED.runtime_job_id, operator_actions.runtime_job_id),
                    agent_run_id = COALESCE(EXCLUDED.agent_run_id, operator_actions.agent_run_id),
                    source_runtime_session_key = COALESCE(EXCLUDED.source_runtime_session_key, operator_actions.source_runtime_session_key),
                    root_session_key = COALESCE(EXCLUDED.root_session_key, operator_actions.root_session_key),
                    workflow_id = COALESCE(EXCLUDED.workflow_id, operator_actions.workflow_id),
                    workflow_step_id = COALESCE(EXCLUDED.workflow_step_id, operator_actions.workflow_step_id),
                    resumed_from_checkpoint_id = COALESCE(EXCLUDED.resumed_from_checkpoint_id, operator_actions.resumed_from_checkpoint_id),
                    resume_launch_mode = COALESCE(EXCLUDED.resume_launch_mode, operator_actions.resume_launch_mode),
                    workspace_root = EXCLUDED.workspace_root,
                    workspace_label = COALESCE(EXCLUDED.workspace_label, operator_actions.workspace_label),
                    target_remote_name = COALESCE(EXCLUDED.target_remote_name, operator_actions.target_remote_name),
                    managed_branch_name = COALESCE(EXCLUDED.managed_branch_name, operator_actions.managed_branch_name),
                    trigger_source = EXCLUDED.trigger_source,
                    intent_kind = EXCLUDED.intent_kind,
                    status = EXCLUDED.status,
                    objective = EXCLUDED.objective,
                    decision_reason = COALESCE(EXCLUDED.decision_reason, operator_actions.decision_reason),
                    result_summary = COALESCE(EXCLUDED.result_summary, operator_actions.result_summary),
                    last_transition_reason = COALESCE(EXCLUDED.last_transition_reason, operator_actions.last_transition_reason),
                    retry_of_action_id = COALESCE(EXCLUDED.retry_of_action_id, operator_actions.retry_of_action_id),
                    replacement_action_id = COALESCE(EXCLUDED.replacement_action_id, operator_actions.replacement_action_id),
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at,
                    completed_at = COALESCE(EXCLUDED.completed_at, operator_actions.completed_at)
                """,
                self._clean_text(action_id),
                int(owner_user_id),
                self._clean_optional_text(runtime_job_id),
                self._clean_optional_text(agent_run_id),
                self._clean_optional_text(source_runtime_session_key),
                self._clean_optional_text(root_session_key),
                self._clean_optional_text(workflow_id),
                self._clean_optional_text(workflow_step_id),
                self._clean_optional_text(resumed_from_checkpoint_id),
                self._clean_optional_text(resume_launch_mode),
                self._clean_text(workspace_root),
                self._clean_optional_text(workspace_label),
                self._clean_optional_text(target_remote_name),
                self._clean_optional_text(managed_branch_name),
                self._clean_text(trigger_source),
                self._clean_text(intent_kind),
                self._clean_text(status, default="queued"),
                str(objective or ""),
                self._clean_optional_text(decision_reason),
                self._clean_optional_text(result_summary),
                self._clean_optional_text(last_transition_reason),
                self._clean_optional_text(retry_of_action_id),
                self._clean_optional_text(replacement_action_id),
                self._dump_json(metadata or {}),
                now,
                float(completed_at) if completed_at is not None else None,
            )
        finally:
            await conn.close()

    async def update_operator_action(
        self,
        *,
        action_id: str,
        runtime_job_id: Optional[str] = None,
        agent_run_id: Optional[str] = None,
        source_runtime_session_key: Optional[str] = None,
        root_session_key: Optional[str] = None,
        workflow_id: Optional[str] = None,
        workflow_step_id: Optional[str] = None,
        resumed_from_checkpoint_id: Optional[str] = None,
        resume_launch_mode: Optional[str] = None,
        workspace_root: Optional[str] = None,
        workspace_label: Optional[str] = None,
        target_remote_name: Optional[str] = None,
        managed_branch_name: Optional[str] = None,
        trigger_source: Optional[str] = None,
        intent_kind: Optional[str] = None,
        status: Optional[str] = None,
        objective: Optional[str] = None,
        decision_reason: Optional[str] = None,
        result_summary: Optional[str] = None,
        last_transition_reason: Optional[str] = None,
        retry_of_action_id: Optional[str] = None,
        replacement_action_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        completed_at: Optional[float] = None,
    ) -> None:
        clean_action_id = self._clean_text(action_id)
        if not clean_action_id:
            return
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            current = await conn.fetchrow(
                """
                SELECT metadata, completed_at, retry_of_action_id, replacement_action_id
                FROM operator_actions
                WHERE action_id = $1
                LIMIT 1
                """,
                clean_action_id,
            )
            if not current:
                return
            merged_metadata = self._load_json(current[0], {})
            if metadata:
                merged_metadata.update(metadata)
            next_completed_at = (
                float(completed_at)
                if completed_at is not None
                else (float(current[1]) if current[1] is not None else None)
            )
            normalized_status = self._clean_text(status).lower()
            if status is not None and normalized_status in {"planned", "queued", "running", "waiting_confirmation"}:
                next_completed_at = None
            elif status is not None and normalized_status in {"completed", "failed", "cancelled", "blocked"} and next_completed_at is None:
                next_completed_at = now
            await conn.execute(
                """
                UPDATE operator_actions
                SET runtime_job_id = COALESCE($2, runtime_job_id),
                    agent_run_id = COALESCE($3, agent_run_id),
                    source_runtime_session_key = COALESCE($4, source_runtime_session_key),
                    root_session_key = COALESCE($5, root_session_key),
                    workflow_id = COALESCE($6, workflow_id),
                    workflow_step_id = COALESCE($7, workflow_step_id),
                    resumed_from_checkpoint_id = COALESCE($8, resumed_from_checkpoint_id),
                    resume_launch_mode = COALESCE($9, resume_launch_mode),
                    workspace_root = COALESCE($10, workspace_root),
                    workspace_label = COALESCE($11, workspace_label),
                    target_remote_name = COALESCE($12, target_remote_name),
                    managed_branch_name = COALESCE($13, managed_branch_name),
                    trigger_source = COALESCE($14, trigger_source),
                    intent_kind = COALESCE($15, intent_kind),
                    status = COALESCE($16, status),
                    objective = COALESCE($17, objective),
                    decision_reason = COALESCE($18, decision_reason),
                    result_summary = COALESCE($19, result_summary),
                    last_transition_reason = COALESCE($20, last_transition_reason),
                    retry_of_action_id = COALESCE($21, retry_of_action_id),
                    replacement_action_id = COALESCE($22, replacement_action_id),
                    metadata = $23::jsonb,
                    updated_at = $24,
                    completed_at = $25
                WHERE action_id = $1
                """,
                clean_action_id,
                self._clean_optional_text(runtime_job_id),
                self._clean_optional_text(agent_run_id),
                self._clean_optional_text(source_runtime_session_key),
                self._clean_optional_text(root_session_key),
                self._clean_optional_text(workflow_id),
                self._clean_optional_text(workflow_step_id),
                self._clean_optional_text(resumed_from_checkpoint_id),
                self._clean_optional_text(resume_launch_mode),
                self._clean_optional_text(workspace_root),
                self._clean_optional_text(workspace_label),
                self._clean_optional_text(target_remote_name),
                self._clean_optional_text(managed_branch_name),
                self._clean_optional_text(trigger_source),
                self._clean_optional_text(intent_kind),
                self._clean_optional_text(status),
                self._clean_optional_text(objective),
                self._clean_optional_text(decision_reason),
                self._clean_optional_text(result_summary),
                self._clean_optional_text(last_transition_reason),
                self._clean_optional_text(retry_of_action_id) or self._clean_optional_text(current[2]),
                self._clean_optional_text(replacement_action_id) or self._clean_optional_text(current[3]),
                self._dump_json(merged_metadata),
                now,
                next_completed_at,
            )
        finally:
            await conn.close()

    async def get_operator_action(self, *, action_id: str) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT action_id, owner_user_id, runtime_job_id, agent_run_id, source_runtime_session_key,
                       root_session_key, workflow_id, workflow_step_id, resumed_from_checkpoint_id, resume_launch_mode,
                       workspace_root, workspace_label, target_remote_name, managed_branch_name, trigger_source, intent_kind, status, objective,
                       decision_reason, result_summary, last_transition_reason, retry_of_action_id,
                       replacement_action_id, metadata, created_at, updated_at, completed_at
                FROM operator_actions
                WHERE action_id = $1
                LIMIT 1
                """,
                self._clean_text(action_id),
            )
            return self._action_record(row) if row else None
        finally:
            await conn.close()

    async def list_operator_actions(
        self,
        *,
        owner_user_id: int,
        statuses: Optional[List[str]] = None,
        workflow_id: Optional[str] = None,
        workspace_root: Optional[str] = None,
        limit: int = 24,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            query = """
                SELECT action_id, owner_user_id, runtime_job_id, agent_run_id, source_runtime_session_key,
                       root_session_key, workflow_id, workflow_step_id, resumed_from_checkpoint_id, resume_launch_mode,
                       workspace_root, workspace_label, target_remote_name, managed_branch_name, trigger_source, intent_kind, status, objective,
                       decision_reason, result_summary, last_transition_reason, retry_of_action_id,
                       replacement_action_id, metadata, created_at, updated_at, completed_at
                FROM operator_actions
                WHERE owner_user_id = $1
            """
            params: List[Any] = [int(owner_user_id)]
            clean_statuses = self._clean_text_list(statuses)
            if clean_statuses:
                placeholders = self._brain._placeholders(len(clean_statuses), start=len(params) + 1)
                params.extend(clean_statuses)
                query += f" AND status IN ({placeholders})"
            clean_workflow_id = self._clean_optional_text(workflow_id)
            if clean_workflow_id:
                params.append(clean_workflow_id)
                query += f" AND workflow_id = ${len(params)}"
            clean_workspace_root = self._clean_optional_text(workspace_root)
            if clean_workspace_root:
                params.append(clean_workspace_root)
                query += f" AND workspace_root = ${len(params)}"
            params.append(max(int(limit or 24), 1))
            query += f" ORDER BY updated_at DESC LIMIT ${len(params)}"
            rows = await conn.fetch(query, *params)
            return [self._action_record(row) for row in rows]
        finally:
            await conn.close()
