"use client";

import { useEffect, useState, useRef, useMemo, useCallback } from "react";
import dynamic from "next/dynamic";
import {
  Search, 
  X, 
  Trash2, 
  Loader2, 
  Maximize2, 
  Minimize2, 
  ZoomIn, 
  ZoomOut, 
  RefreshCcw,
  Type
} from "lucide-react";
import { apiDelete, apiGet } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { devLogError } from "@/lib/devLog";

// Dynamic import for ForceGraph to avoid SSR issues
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center h-full w-full text-white/20">
      <Loader2 className="h-8 w-8 animate-spin" />
    </div>
  ),
});

type Memory = {
  id: number;
  memory_type: string;
  content: string;
  importance_score: number;
  created_at: number;
  tags: string[];
};

type GraphNode = {
  id: number;
  val: number; // radius size
  color: string;
  label: string; // Used for tooltip
  categoryLabel: string;
  memory: Memory; // Raw data
  x?: number;
  y?: number;
};

type GraphLink = {
  source: number;
  target: number;
  color: string;
};

export function MemoryGalaxy({ compact = false }: { compact?: boolean }) {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [isTitleExpanded, setIsTitleExpanded] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showLabels, setShowLabels] = useState(true);
  
  const graphRef = useRef<any>(null);
  const graphWrapperRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });

  // Fetch data
  const fetchData = useCallback(async () => {
      setLoading(true);
      try {
        const data = await apiGet<{ memories: Memory[] }>("/api/web/account/memory");
        setMemories(data.memories || []);
      } catch (e) {
        devLogError("Failed to load memories", e);
      } finally {
        setLoading(false);
      }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Reset expanded state when selection changes
  useEffect(() => {
    setIsTitleExpanded(false);
  }, [selectedNode?.id]);

  // Listen for external refresh events (e.g. from BotControls)
  useEffect(() => {
    const onRefresh = () => fetchData();
    window.addEventListener("refresh_memory_galaxy", onRefresh);
    return () => window.removeEventListener("refresh_memory_galaxy", onRefresh);
  }, [fetchData]);

  // Resize handler
  useEffect(() => {
    if (!graphWrapperRef.current) return;
    
    const obs = new ResizeObserver((entries) => {
      if (entries.length === 0) return;
      const { width, height } = entries[0].contentRect;
      setDimensions({ width, height });
    });

    obs.observe(graphWrapperRef.current);
    return () => obs.disconnect();
  }, [isFullscreen]);

  // Transform memories to graph data
  const graphData = useMemo(() => {
    // 1. Filter first
    let filtered = memories;
    if (searchQuery) {
        const lower = searchQuery.toLowerCase();
        filtered = memories.filter(m => 
            m.content.toLowerCase().includes(lower) || 
            m.tags.some(t => t.toLowerCase().includes(lower))
        );
    }

    const classify = (rawType: string, content: string) => {
      const t = (rawType || "").toLowerCase();
      const c = (content || "").toLowerCase();

      // Canonical categories
      if (t.includes("preference")) return { key: "preference", label: "Preferences", color: "#ec4899" };
      if (t.includes("people") || t.includes("identity") || t.includes("background")) return { key: "people", label: "People", color: "#0ea5e9" };
      if (t.includes("project") || t.includes("work") || t.includes("job") || t.includes("career")) return { key: "project", label: "Projects & Work", color: "#2563eb" };
      if (t.includes("moment") || t.includes("history") || t.includes("event") || t.includes("update")) return { key: "moment", label: "Moments", color: "#eab308" };

      // Legacy categories that used to be stored by the backend
      if (t.includes("fact")) return { key: "people", label: "People", color: "#0ea5e9" };

      // Heuristic fallback for "other"/unknown types
      if (
        c.includes("today") ||
        c.includes("yesterday") ||
        c.includes("last ") ||
        c.includes("this morning") ||
        c.includes("this week") ||
        c.includes("just ") ||
        c.includes("started") ||
        c.includes("finished")
      ) {
        return { key: "moment", label: "Moments", color: "#eab308" };
      }
      if (
        c.includes("working on") ||
        c.includes("building") ||
        c.includes("project") ||
        c.includes("job") ||
        c.includes("work")
      ) {
        return { key: "project", label: "Projects & Work", color: "#2563eb" };
      }
      if (
        c.includes("likes") ||
        c.includes("love") ||
        c.includes("hate") ||
        c.includes("prefer")
      ) {
        return { key: "preference", label: "Preferences", color: "#ec4899" };
      }

      return { key: "unknown", label: "Uncategorized", color: "#ffffff" };
    };

    const nodes: GraphNode[] = filtered.map((mem) => {
      const category = classify(mem.memory_type, mem.content);

      // Importance logic: Ensure it is a valid number >= 0.1
      const score = Number.isFinite(mem.importance_score) ? Math.max(0.1, mem.importance_score) : 0.1;

      return {
        id: mem.id,
        val: score, 
        color: category.color,
        label: mem.content.length > 50 ? mem.content.substring(0, 50) + "..." : mem.content,
        categoryLabel: category.label,
        memory: mem
      };
    });

    const links: GraphLink[] = [];
    const nodeIds = new Set(nodes.map(n => n.id));

    // Create links (O(N^2) but N is usually small < 1000)
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        
        let strength = 0;

        // Link by tags
        const commonTags = a.memory.tags.filter(t => b.memory.tags.includes(t));
        if (commonTags.length > 0) strength += commonTags.length;

        // Link by category
        if (a.categoryLabel === b.categoryLabel) strength += 0.5;

        if (strength > 0) {
            links.push({
                source: a.id,
                target: b.id,
                color: `rgba(255,255,255,${Math.min(0.2, strength * 0.05)})`
            });
        }
      }
    }

    return { nodes, links };
  }, [memories, searchQuery]);

  const handleDelete = async () => {
    if (!selectedNode) return;
    const id = selectedNode.memory.id;
    
    // Optimistic remove
    setMemories(prev => prev.filter(m => m.id !== id));
    setSelectedNode(null);

    try {
      await apiDelete(`/api/web/account/memory/${id}`);
    } catch (e) {
      devLogError("Failed to delete", e);
      // Revert if needed, but for now simplistic is okay
    }
  };

  const handleZoom = (k: number) => {
      if (graphRef.current) {
          // Current zoom
          const currentZoom = graphRef.current.zoom();
          graphRef.current.zoom(currentZoom * k, 400);
      }
  };

  return (
    <div 
      className={`relative w-full h-full flex flex-col rounded-2xl border border-white/5 bg-black/40 backdrop-blur-md overflow-hidden shadow-2xl transition-all duration-500 ${isFullscreen ? "fixed inset-0 z-50 bg-black/95" : ""}`}
    >
      {/* Top Bar */}
      <div className={`absolute top-0 left-0 right-0 p-4 flex items-start justify-between z-10 pointer-events-none ${compact ? "hidden" : ""}`}>
        
        {/* Search */}
        <div className="pointer-events-auto relative group">
          <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none">
            <Search className="h-4 w-4 text-white/30 group-focus-within:text-white/80 transition-colors" />
          </div>
          <input 
            type="text" 
            placeholder="Search memory..." 
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-10 w-64 rounded-full bg-black/50 border border-white/10 pl-10 pr-4 text-sm text-white placeholder:text-white/20 focus:outline-none focus:border-white/30 focus:ring-1 focus:ring-white/20 transition-all backdrop-blur-xl"
          />
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-2 pointer-events-auto">
            <button 
                onClick={fetchData} 
                className="p-2.5 rounded-full bg-black/50 border border-white/10 text-white/60 hover:text-white hover:bg-white/10 transition-colors"
                title="Refresh Memory"
            >
                <RefreshCcw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button 
                onClick={() => setShowLabels(!showLabels)} 
                className={`p-2.5 rounded-full bg-black/50 border border-white/10 transition-colors ${showLabels ? 'text-white bg-white/10' : 'text-white/60 hover:text-white hover:bg-white/10'}`}
                title="Toggle Labels"
            >
                <Type className="h-4 w-4" />
            </button>
             <button 
                onClick={() => setIsFullscreen(!isFullscreen)} 
                className="p-2.5 rounded-full bg-black/50 border border-white/10 text-white/60 hover:text-white hover:bg-white/10 transition-colors"
                title="Toggle Fullscreen"
            >
                {isFullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </button>
        </div>
      </div>

      {/* Graph Area */}
      <div 
        ref={graphWrapperRef}
        className="flex-1 w-full min-h-0 relative overflow-hidden cursor-move bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-gray-900/20 via-black/0 to-black/0"
      >
        <ForceGraph2D
            ref={graphRef}
            width={dimensions.width}
            height={dimensions.height}
            graphData={graphData}
            nodeLabel="label"
            nodeColor="color"
            // Ensure minimum visible size even for low importance
            nodeVal={(node: any) => Math.max(1, node.val * 5)} 
            linkColor="color"
            backgroundColor="rgba(0,0,0,0)"
            onNodeClick={(node: any) => {
                setSelectedNode(node);
                graphRef.current?.centerAt(node.x, node.y, 1000);
                graphRef.current?.zoom(4, 2000);
            }}
            onBackgroundClick={() => setSelectedNode(null)}
            d3AlphaDecay={0.02}
            d3VelocityDecay={0.4}
            warmupTicks={100}
            cooldownTicks={100}
            nodeCanvasObject={(node: any, ctx, globalScale) => {
              if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) return;

              const val = Number.isFinite(node.val) ? Math.max(0.1, node.val) : 0.1;
              const r = Math.sqrt(val) * 4; // Base radius size
              
              // Draw Glow
              const gradient = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, r * 3);
              gradient.addColorStop(0, `${node.color}40`); // 25% opacity
              gradient.addColorStop(1, 'transparent');
              ctx.fillStyle = gradient;
              ctx.beginPath();
              ctx.arc(node.x, node.y, r * 3, 0, 2 * Math.PI, false);
              ctx.fill();

              // Draw Core
              ctx.fillStyle = node.color;
              ctx.beginPath();
              ctx.arc(node.x, node.y, r, 0, 2 * Math.PI, false);
              ctx.fill();

              // Selection Ring
              if (selectedNode && selectedNode.id === node.id) {
                ctx.strokeStyle = '#ffffff';
                ctx.lineWidth = 2 / globalScale;
                ctx.beginPath();
                ctx.arc(node.x, node.y, r * 1.5, 0, 2 * Math.PI, false);
                ctx.stroke();
              }
              
              // Text Label (only if zoomed in or large node)
              if (showLabels && (globalScale > 1.5 || val > 0.5)) {
                  const fontSize = 12 / globalScale;
                  ctx.font = `${fontSize}px Sans-Serif`;
                  ctx.textAlign = 'center';
                  ctx.textBaseline = 'middle';
                  ctx.fillStyle = 'rgba(255,255,255,0.8)';
                  // Draw below node
                  ctx.fillText(node.label, node.x, node.y + r + fontSize);
              }
            }}
          />
      </div>
      
      {/* Zoom Controls (Bottom Right) */}
      <div className="absolute bottom-6 right-6 flex flex-col gap-2 z-10 pointer-events-auto">
          <button onClick={() => handleZoom(1.5)} className="p-2 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-white/70 transition-colors">
              <ZoomIn className="h-4 w-4" />
          </button>
          <button onClick={() => handleZoom(0.75)} className="p-2 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-white/70 transition-colors">
              <ZoomOut className="h-4 w-4" />
          </button>
      </div>

      {/* Legend (Bottom Left) */}
      <div className={`absolute bottom-6 left-6 pointer-events-none flex flex-col gap-2 p-4 rounded-xl bg-black/20 border border-white/5 backdrop-blur-sm ${compact ? "hidden" : ""}`}>
	        {[
	          { label: "Preferences", color: "#ec4899" },
	          { label: "People", color: "#0ea5e9" },
	          { label: "Projects & Work", color: "#2563eb" },
	          { label: "Moments", color: "#eab308" },
	        ].map(item => (
	          <div key={item.label} className="flex items-center gap-2">
	            <div className="w-2 h-2 rounded-full shadow-[0_0_8px]" style={{ backgroundColor: item.color, boxShadow: `0 0 8px ${item.color}` }} />
	            <span className="text-[10px] text-white/60 uppercase tracking-widest">{item.label}</span>
          </div>
        ))}
      </div>

      {/* Detail Panel */}
      <AnimatePresence>
        {!compact && selectedNode && (
          <motion.div 
            initial={{ x: 400, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
             exit={{ x: 400, opacity: 0 }}
             className="absolute top-20 bottom-20 right-4 w-80 rounded-2xl border border-white/10 bg-black/80 backdrop-blur-xl shadow-2xl z-20 flex flex-col overflow-hidden"
          >
             {/* Gradient Header */}
             <div className={`p-6 flex flex-col justify-end relative overflow-hidden ${isTitleExpanded ? "min-h-[8rem]" : "h-32"}`}>
                <div 
                    className="absolute inset-0 opacity-20"
                    style={{ backgroundColor: selectedNode.color }}
                />
                <div className="absolute inset-0 bg-gradient-to-b from-transparent to-black/90" />
                
                <div className="relative z-10">
                    <div className="flex items-center justify-between mb-2">
                         <span 
                            className="text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded border border-white/20"
                            style={{ color: selectedNode.color, backgroundColor: `${selectedNode.color}10` }}
                         >
                            {selectedNode.categoryLabel}
                         </span>
                         <button onClick={() => setSelectedNode(null)} className="text-white/50 hover:text-white">
                             <X className="h-4 w-4" />
                         </button>
                    </div>
                    <h3 className={`text-white font-medium text-lg leading-tight ${isTitleExpanded ? "" : "line-clamp-2"}`}>
                        {selectedNode.memory.content}
                    </h3>
                    {selectedNode.memory.content.length > 100 && (
                      <button
                        type="button"
                        onClick={() => setIsTitleExpanded((v) => !v)}
                        className="mt-2 text-[10px] uppercase tracking-widest text-white/50 hover:text-white/80"
                      >
                        {isTitleExpanded ? "Collapse" : "Expand"}
                      </button>
                    )}
                </div>
             </div>

             <div className="flex-1 p-6 overflow-y-auto space-y-6">
                
                {/* Metrics */}
                <div className="grid grid-cols-2 gap-4">
                    <div className="p-3 rounded-lg bg-white/5 border border-white/5">
                        <div className="text-[10px] uppercase text-white/40 mb-1">Impact</div>
                        <div className="text-xl font-light text-white">{(selectedNode.memory.importance_score * 10).toFixed(1)}<span className="text-sm opacity-50">/10</span></div>
                    </div>
                    <div className="p-3 rounded-lg bg-white/5 border border-white/5">
                        <div className="text-[10px] uppercase text-white/40 mb-1">Age</div>
                        <div className="text-xl font-light text-white">
                            {Math.floor((Date.now()/1000 - selectedNode.memory.created_at) / 86400)}<span className="text-sm opacity-50">d</span>
                        </div>
                    </div>
                </div>

                {/* Tags */}
                {selectedNode.memory.tags.length > 0 && (
                    <div>
                        <div className="text-[10px] uppercase tracking-widest text-white/30 mb-2">Associations</div>
                        <div className="flex flex-wrap gap-2">
                            {selectedNode.memory.tags.map(tag => (
                                <span key={tag} className="text-xs px-2.5 py-1 rounded-md bg-white/5 text-white/70 border border-white/5">
                                    #{tag}
                                </span>
                            ))}
                        </div>
                    </div>
                )}
             </div>

             <div className="p-4 border-t border-white/5 bg-black/40">
                <button 
                    onClick={handleDelete}
                    className="flex items-center justify-center gap-2 w-full py-2.5 rounded-lg bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-all text-xs font-medium uppercase tracking-wide border border-red-500/20"
                >
                    <Trash2 className="h-3.5 w-3.5" />
                    Delete Memory
                </button>
             </div>

          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
