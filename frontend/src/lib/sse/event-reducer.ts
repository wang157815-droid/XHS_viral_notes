import type { CanvasModule, CanvasSchema, TaskEvent, TaskStatus } from "@/lib/contracts";

export interface TaskLogEntry {
  event_id: string;
  timestamp: string;
  level: "info" | "warn" | "error";
  message: string;
  branch_id?: string | null;
  agent_id?: string;
}

export interface AgentStatusEntry {
  agent_id: string;
  message: string;
  lastAt: string;
  done: boolean;
}

export interface TaskStreamState {
  status: TaskStatus | "unknown";
  progress: number;
  canvas: CanvasSchema | null;
  logs: TaskLogEntry[];
  agentStatus: Record<string, AgentStatusEntry>;
  lastEventAt: string | null;
  error: { code: string; message: string } | null;
  /** 视频异步支路状态(阶段 4.2): pending / completed / partial(部分失败) / failed */
  videoAsyncState: "idle" | "pending" | "completed" | "partial" | "failed";
}

export function initialTaskStreamState(): TaskStreamState {
  return {
    status: "unknown",
    progress: 0,
    canvas: null,
    logs: [],
    agentStatus: {},
    lastEventAt: null,
    error: null,
    videoAsyncState: "idle",
  };
}

export function reduceTaskEvent(
  state: TaskStreamState,
  event: TaskEvent,
): TaskStreamState {
  const next: TaskStreamState = {
    ...state,
    logs: state.logs,
    agentStatus: state.agentStatus,
    lastEventAt: event.timestamp || state.lastEventAt,
  };

  switch (event.type) {
    case "task_status": {
      const payload = event.payload as { status?: TaskStatus; progress?: number };
      next.status = payload.status ?? state.status;
      next.progress = typeof payload.progress === "number" ? payload.progress : state.progress;
      break;
    }
    case "agent_progress": {
      const payload = event.payload as { agent_id?: string; message?: string; progress?: number };
      next.progress = typeof payload.progress === "number" ? payload.progress : state.progress;
      const agentId = payload.agent_id ?? "agent";
      const message = payload.message ?? "";
      next.logs = appendLog(next.logs, {
        event_id: event.event_id,
        timestamp: event.timestamp,
        level: "info",
        message: `[${agentId}] ${message}`,
        branch_id: event.branch_id,
        agent_id: agentId,
      });
      next.agentStatus = {
        ...state.agentStatus,
        [agentId]: {
          agent_id: agentId,
          message,
          lastAt: event.timestamp,
          done: state.agentStatus[agentId]?.done ?? false,
        },
      };
      break;
    }
    case "log": {
      const payload = event.payload as { level?: TaskLogEntry["level"]; message?: string };
      next.logs = appendLog(next.logs, {
        event_id: event.event_id,
        timestamp: event.timestamp,
        level: payload.level ?? "info",
        message: payload.message ?? "",
        branch_id: event.branch_id,
      });
      break;
    }
    case "canvas_schema_updated": {
      const raw = event.payload as Record<string, unknown> | null | undefined;
      // SSE 超大事件会被后端替换成 {truncated: true}；此时 payload 可能为空对象，绝不能覆盖已有画布。
      if (
        !raw ||
        raw["truncated"] === true ||
        typeof raw["task_id"] !== "string" ||
        !Array.isArray(raw["modules"])
      ) {
        break;
      }
      const canvas = raw as unknown as CanvasSchema;
      next.canvas = canvas;
      // canvas 渲染完成，说明编排链路走完，把所有曾经报过 progress 的 agent 标记为 done
      const merged: Record<string, AgentStatusEntry> = {};
      for (const [aid, entry] of Object.entries(state.agentStatus)) {
        merged[aid] = { ...entry, done: true };
      }
      next.agentStatus = merged;
      break;
    }
    case "canvas_module_updated": {
      if (state.canvas) {
        const payload = event.payload as Partial<CanvasModule> & { module_id?: string };
        next.canvas = applyModulePatch(state.canvas, payload);
      }
      break;
    }
    case "error": {
      const payload = event.payload as { code?: string; message?: string };
      next.error = { code: payload.code ?? "SYSTEM_INTERNAL", message: payload.message ?? "" };
      next.logs = appendLog(next.logs, {
        event_id: event.event_id,
        timestamp: event.timestamp,
        level: "error",
        message: `${payload.code ?? "SYSTEM_INTERNAL"}: ${payload.message ?? ""}`,
      });
      break;
    }
    case "done": {
      next.progress = Math.max(next.progress, 100);
      if (next.status === "running") next.status = "completed";
      // 标记所有已知 agent 为 done
      const merged: Record<string, AgentStatusEntry> = {};
      for (const [aid, entry] of Object.entries(state.agentStatus)) {
        merged[aid] = { ...entry, done: true };
      }
      next.agentStatus = merged;
      // 阶段 4.2: DONE 若带 video_pending=true,视频支路仍在后台
      const videoPending = Boolean(
        (event.payload as { video_pending?: boolean } | undefined)?.video_pending,
      );
      if (videoPending) next.videoAsyncState = "pending";
      break;
    }
    case "task_video_done": {
      // 阶段 4.2: 视频异步支路完结事件
      const payload = event.payload as
        | { total?: number; completed?: number; failed?: number; reason?: string }
        | undefined;
      const total = Number(payload?.total ?? 0);
      const completed = Number(payload?.completed ?? 0);
      const failed = Number(payload?.failed ?? 0);
      if (payload?.reason === "cancelled" || payload?.reason === "error") {
        next.videoAsyncState = "failed";
      } else if (failed > 0 && completed > 0) {
        next.videoAsyncState = "partial";
      } else if (completed === 0 && total > 0) {
        next.videoAsyncState = "failed";
      } else {
        next.videoAsyncState = "completed";
      }
      next.logs = appendLog(next.logs, {
        event_id: event.event_id,
        timestamp: event.timestamp,
        level: failed > 0 ? "warn" : "info",
        message: `[视频异步] 完成 ${completed}/${total}${failed > 0 ? ` · 失败 ${failed}` : ""}`,
      });
      break;
    }
    case "ping":
    default:
      break;
  }

  return next;
}

function appendLog(logs: TaskLogEntry[], entry: TaskLogEntry): TaskLogEntry[] {
  const next = [...logs, entry];
  if (next.length > 200) {
    return next.slice(-150);
  }
  return next;
}

function applyModulePatch(
  canvas: CanvasSchema,
  patch: Partial<CanvasModule> & { module_id?: string },
): CanvasSchema {
  if (!patch.module_id) return canvas;
  const modules = canvas.modules.map((module) => {
    if (module.module_id !== patch.module_id) return module;
    return { ...module, ...patch } as CanvasModule;
  });
  return { ...canvas, modules };
}
