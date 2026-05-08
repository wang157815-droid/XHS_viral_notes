"use client";

import { useEffect, useMemo, useReducer, useRef, useState } from "react";

import type { TaskEvent } from "@/lib/contracts";
import { TaskEventStream, type SseClientStatus } from "./event-source-client";
import {
  initialTaskStreamState,
  reduceTaskEvent,
  type TaskStreamState,
} from "./event-reducer";

interface StreamStateEnvelope {
  taskId: string | null;
  state: TaskStreamState;
}

interface UseTaskStreamResult {
  state: TaskStreamState;
  connectionStatus: SseClientStatus;
  lastError: { code: string; message: string } | null;
  reconnect: () => void;
}

export function useTaskStream(
  taskId: string | null,
  options: { enabled?: boolean } = {},
): UseTaskStreamResult {
  const enabled = options.enabled ?? true;
  const [state, dispatch] = useReducer(
    (current: StreamStateEnvelope, action: { taskId: string; event: TaskEvent }) => {
      const base = current.taskId === action.taskId ? current.state : initialTaskStreamState();
      return { taskId: action.taskId, state: reduceTaskEvent(base, action.event) };
    },
    undefined,
    () => ({ taskId: null, state: initialTaskStreamState() }),
  );
  const [connectionStatus, setConnectionStatus] = useState<{
    taskId: string | null;
    status: SseClientStatus;
  }>({ taskId: null, status: "idle" });
  const [lastError, setLastError] = useState<{
    taskId: string | null;
    error: { code: string; message: string } | null;
  }>({ taskId: null, error: null });
  const streamRef = useRef<TaskEventStream | null>(null);
  const [reconnectTick, setReconnectTick] = useState(0);

  useEffect(() => {
    if (!enabled || !taskId) {
      return;
    }

    const stream = new TaskEventStream({
      taskId,
      onEvent: (event) => dispatch({ taskId, event }),
      onError: (err) => {
        if (err.code === "SSE_IDLE_TIMEOUT") {
          return;
        }
        setLastError({ taskId, error: { code: err.code, message: err.message } });
      },
      onStatusChange: (s) => {
        setConnectionStatus({ taskId, status: s });
        if (s === "open") {
          setLastError({ taskId, error: null });
        }
      },
    });
    streamRef.current = stream;
    stream.start();

    return () => {
      stream.close();
      streamRef.current = null;
    };
  }, [taskId, enabled, reconnectTick]);

  const reconnect = useMemo(
    () => () => {
      setLastError({ taskId, error: null });
      setReconnectTick((v) => v + 1);
    },
    [taskId],
  );

  const scopedState = state.taskId === taskId ? state.state : initialTaskStreamState();
  const scopedStatus = connectionStatus.taskId === taskId ? connectionStatus.status : "idle";
  const scopedError = lastError.taskId === taskId ? lastError.error : null;

  return { state: scopedState, connectionStatus: scopedStatus, lastError: scopedError, reconnect };
}
