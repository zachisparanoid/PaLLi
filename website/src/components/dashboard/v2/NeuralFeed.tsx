"use client";

import { useEffect, useState, useRef } from "react";
import {
  Terminal, 
  Activity, 
  BrainCircuit, 
  Search, 
  MessageSquare,
  Zap,
  Clock,
  Wifi,
  WifiOff
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { devLogError } from "@/lib/devLog";
import { getBackendWebSocketUrl } from "@/lib/api";

type EventType =
  | "processing_start"
  | "intent_detected"
  | "memory_retrieved"
  | "response_generated"
  | "proactive_check"
  | "agentic_event"
  | "runtime_update";

interface NeuralEvent {
  id: string;
  type: EventType;
  timestamp: number;
  data: any;
}

const EVENT_ICONS: Record<EventType, any> = {
  processing_start: MessageSquare,
  intent_detected: BrainCircuit,
  memory_retrieved: Search,
  response_generated: Zap,
  proactive_check: Clock,
  agentic_event: Terminal,
  runtime_update: Activity,
};

const EVENT_COLORS: Record<EventType, string> = {
  processing_start: "text-brand-300",
  intent_detected: "text-brand-500",
  memory_retrieved: "text-amber-400",
  response_generated: "text-brand-400",
  proactive_check: "text-white/40",
  agentic_event: "text-emerald-300",
  runtime_update: "text-cyan-200",
};

const EVENT_DOT_COLORS: Record<EventType, string> = {
  processing_start: "bg-brand-300",
  intent_detected: "bg-brand-500",
  memory_retrieved: "bg-amber-400",
  response_generated: "bg-brand-400",
  proactive_check: "bg-white/30",
  agentic_event: "bg-emerald-300",
  runtime_update: "bg-cyan-300",
};

export function NeuralFeed() {
  const [events, setEvents] = useState<NeuralEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const seenIdsRef = useRef<Set<string>>(new Set());
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    const wsUrl = getBackendWebSocketUrl("/api/chat/ws/stream");
    
    const connect = () => {
      if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) return;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        // console.log("Connected to Neural Stream");
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          
          // Generate ID if missing (server should send one ideally)
          // Use deterministic ID based on timestamp to dedup identical events
          const evtId = payload.id || (payload.timestamp 
            ? `${payload.type}-${payload.timestamp}` 
            : Math.random().toString(36).substr(2, 9) + Date.now());
          
          if (seenIdsRef.current.has(evtId)) return;
          seenIdsRef.current.add(evtId);
          
          // Cleanup old IDs to prevent memory leak in long sessions
          if (seenIdsRef.current.size > 1000) {
             const it = seenIdsRef.current.values();
             for (let i = 0; i < 500; i++) {
               const val = it.next().value;
               if (val !== undefined) seenIdsRef.current.delete(val);
             }
          }

          const newEvent: NeuralEvent = {
            id: evtId,
            type: payload.type,
            timestamp: payload.timestamp || Date.now() / 1000,
            data: payload.data
          };
          
          setEvents(prev => [newEvent, ...prev].slice(0, 50));
        } catch (e) {
          devLogError("Failed to parse stream event", e);
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        wsRef.current = null;
        reconnectTimerRef.current = setTimeout(connect, 3000);
      };

      ws.onerror = (err) => {
          devLogError("WS Error", err);
          ws.close();
      };
    };

    connect();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
    };
  }, []);

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-black/40 border border-white/5 rounded-2xl backdrop-blur-md overflow-hidden shadow-xl">
      {/* Header */}
      <div className="h-12 border-b border-white/5 bg-white/[0.02] flex items-center justify-between px-4">
        <div className="flex items-center gap-2 text-xs font-mono tracking-widest uppercase text-white/40">
          <Terminal className="h-3.5 w-3.5" />
          <span>Activity Feed</span>
        </div>
        <div className="flex items-center gap-2">
            {isConnected ? (
                <Wifi className="h-3.5 w-3.5 text-brand-500/80" />
            ) : (
                <WifiOff className="h-3.5 w-3.5 text-red-500/50" />
            )}
        </div>
      </div>

      {/* Feed */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4 scrollbar-thin scrollbar-thumb-white/10 scrollbar-track-transparent">
        <AnimatePresence initial={false} mode="popLayout">
          {events.length === 0 ? (
            <motion.div 
                initial={{ opacity: 0 }} 
                animate={{ opacity: 1 }}
                className="h-full flex flex-col items-center justify-center text-white/20 gap-3"
            >
              <Activity className="h-8 w-8 animate-pulse duration-[3000ms]" />
              <span className="text-xs font-mono">Waiting for activity...</span>
            </motion.div>
            ) : (
            events.map((evt) => {
              const Icon = EVENT_ICONS[evt.type] || Activity;
              const colorClass = EVENT_COLORS[evt.type] || "text-white/50";
              const dotClass = EVENT_DOT_COLORS[evt.type] || "bg-white/20";
              
              return (
                <motion.div
                  key={evt.id}
                  layout
                  initial={{ opacity: 0, x: -20, scale: 0.95 }}
                  animate={{ opacity: 1, x: 0, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.9 }}
                  className="group relative pl-4 border-l border-white/10"
                >
                    {/* Timestamp dot */}
                    <div className={`absolute -left-[3px] top-1.5 h-1.5 w-1.5 rounded-full ${dotClass} ring-4 ring-black`} />
                    
                    <div className="flex items-center gap-2 mb-1">
                        <div className={`flex h-5 w-5 items-center justify-center rounded-md bg-white/5 border border-white/10 ${colorClass}`}>
                          <Icon className="h-3.5 w-3.5" />
                        </div>
                        <span className={`text-[10px] font-mono uppercase tracking-wider opacity-70 ${colorClass}`}>
                            {evt.type.replace(/_/g, " ")}
                        </span>
                        <span className="text-[10px] text-white/20 font-mono ml-auto">
                            {new Date(evt.timestamp * 1000).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute:'2-digit', second:'2-digit' })}
                        </span>
                    </div>

                    <div className="text-sm text-white/80 font-light leading-relaxed break-words">
                         {renderEventData(evt)}
                    </div>
                </motion.div>
              );
            })
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function renderEventData(evt: NeuralEvent) {
    if (evt.type === 'processing_start') {
        return (
            <span className="text-white/60">
                User: <span className="text-white">"{evt.data?.content || 'Unknown'}"</span>
            </span>
        );
    }
    if (evt.type === 'response_generated') {
         return (
            <span className="text-brand-400/80">
                PaLLi: <span className="text-brand-50">"{evt.data?.content || 'Unknown'}"</span>
            </span>
        );
    }
    if (evt.type === 'intent_detected') {
        return (
            <div className="flex items-center gap-2">
                <span className="px-1.5 py-0.5 rounded bg-brand-500/15 border border-brand-500/25 text-brand-100 text-xs">
                    {evt.data?.intent || 'Unknown'}
                </span>
                <span className="text-xs text-white/30">
                    {(evt.data?.confidence * 100).toFixed(0)}% confidence
                </span>
            </div>
        );
    }
    if (evt.type === 'agentic_event') {
        const stream = evt.data?.stream || "agent";
        const payload = evt.data?.data || {};
        if (stream === "lifecycle") {
            const phase = payload.phase || "event";
            const status = payload.status ? ` · ${payload.status}` : "";
            return (
                <span className="text-emerald-100/80">
                    Run <span className="font-mono text-emerald-200">{String(evt.data?.run_id || "").slice(0, 8)}</span>{" "}
                    {phase === "start" ? "started" : phase === "end" ? "completed" : "errored"}
                    <span className="text-white/40">{status}</span>
                </span>
            );
        }
        if (stream === "tool") {
            if (payload.phase === "update") {
                return (
                    <span className="text-emerald-100/80">
                        Tool <span className="font-mono text-emerald-200">{payload.tool_name || "unknown"}</span>{" "}
                        update
                        {payload.stream_name ? <span className="text-white/40"> · {payload.stream_name}</span> : null}
                        {payload.update_kind ? <span className="text-white/40"> · {payload.update_kind}</span> : null}
                        {payload.text ? <span className="text-white/70">: "{String(payload.text).slice(0, 220)}"</span> : null}
                    </span>
                );
            }
            return (
                <span className="text-emerald-100/80">
                    Tool <span className="font-mono text-emerald-200">{payload.tool_name || "unknown"}</span>{" "}
                    {payload.phase === "start" ? "started" : "finished"}
                    {payload.error ? <span className="text-red-300/80"> · {payload.error}</span> : null}
                </span>
            );
        }
        if (stream === "assistant") {
            if (payload.phase === "start") {
                return <span className="text-emerald-100/80">Agent reply started</span>;
            }
            if (payload.phase === "delta") {
                return (
                    <span className="text-emerald-100/80">
                        Agent streaming: <span className="text-white/70">"{payload.text_so_far || payload.delta_text || ""}"</span>
                    </span>
                );
            }
            return (
                <span className="text-emerald-100/80">
                    Agent reply: <span className="text-white/70">"{payload.text || "No text"}"</span>
                </span>
            );
        }
    }
    if (evt.type === "runtime_update") {
        return (
            <span className="text-cyan-100/80">
                Runtime surface updated
                {evt.data?.section ? <span className="text-white/40"> · {evt.data.section}</span> : null}
            </span>
        );
    }
    
    // Default JSON dump for others, formatted nicely
    const jsonStr = JSON.stringify(evt.data);
    if (jsonStr.length < 50) return <span className="font-mono text-xs opacity-50">{jsonStr}</span>;
    return <span className="font-mono text-xs opacity-50 truncate block">{jsonStr}</span>;
}
