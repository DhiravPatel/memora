"use client";

import { useMemo } from "react";
import ReactFlow, { Background, BackgroundVariant, Controls, MarkerType, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";

import { EmptyState } from "@/components/ui/states";
import type { GraphResponse } from "@/lib/types";
import { THEME } from "@/lib/utils";

/* One hue per entity type, carried by the node's left rail. Same hues as the badges. */
const TYPE_COLORS: Record<string, string> = {
  customer: THEME.accent,
  integration: THEME.info,
  feature: THEME.warning,
  subscription: THEME.success,
  support_ticket: THEME.danger,
  company: THEME.violet,
  employee: THEME.violet,
  order: THEME.info,
};

export function MemoryGraphView({ graph }: { graph: GraphResponse }) {
  const { nodes, edges } = useMemo(() => {
    // Radial layout around the customer: no layout engine needed for a one-hop graph.
    const others = graph.nodes.filter((node) => !node.is_root);
    const radius = Math.max(190, others.length * 26);

    const nodes: Node[] = graph.nodes.map((node) => {
      const color = TYPE_COLORS[node.type] ?? THEME.mutedForeground;
      if (node.is_root) {
        return {
          id: node.id,
          position: { x: 0, y: 0 },
          data: { label: node.label.toUpperCase() },
          style: {
            background: THEME.accent,
            color: "#ffffff",
            border: "none",
            borderRadius: 10,
            boxShadow: "0 10px 24px -12px rgba(200, 30, 60, 0.6)",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            letterSpacing: "0.1em",
            fontWeight: 700,
            padding: "10px 16px",
          },
        } satisfies Node;
      }
      const position = others.findIndex((other) => other.id === node.id);
      const angle = (position / Math.max(1, others.length)) * Math.PI * 2;
      return {
        id: node.id,
        position: { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius },
        data: { label: `${node.label}\n${node.type.replace(/_/g, " ").toUpperCase()}` },
        style: {
          background: THEME.surface,
          color: THEME.foreground,
          border: `1px solid ${THEME.border}`,
          borderLeft: `3px solid ${color}`,
          boxShadow: "0 1px 2px rgba(26, 22, 19, 0.06), 0 8px 20px -14px rgba(26, 22, 19, 0.3)",
          borderRadius: 8,
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          lineHeight: 1.5,
          padding: "8px 12px",
          whiteSpace: "pre-line",
          textAlign: "left",
        },
      } satisfies Node;
    });

    const edges: Edge[] = graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.label.toUpperCase(),
      labelStyle: {
        fontSize: 9,
        fontFamily: "var(--font-mono)",
        letterSpacing: "0.08em",
        fill: THEME.mutedForeground,
      },
      labelBgStyle: { fill: THEME.surface },
      labelBgPadding: [5, 3] as [number, number],
      labelBgBorderRadius: 4,
      style: {
        stroke: edge.label.includes("problem") ? THEME.accent : THEME.mutedForeground,
        strokeWidth: Math.max(1, edge.confidence * 2.5),
        strokeDasharray: edge.confidence < 0.5 ? "4 3" : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: THEME.mutedForeground },
      animated: edge.label.includes("problem"),
    }));

    return { nodes, edges };
  }, [graph]);

  if (!graph.nodes.length) {
    return (
      <EmptyState
        title="No graph yet"
        description="Entities and relationships appear once events mention integrations, plans or features."
      />
    );
  }

  return (
    <div className="h-[520px] w-full bg-surface-2">
      <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
        {/* Dots rather than a grid: enough to show the canvas moving, without boxing it. */}
        <Background variant={BackgroundVariant.Dots} gap={28} size={1} color={THEME.border} />
        <Controls
          showInteractive={false}
          className="!overflow-hidden !rounded-md !border !border-border !bg-surface !shadow-sm"
        />
      </ReactFlow>
    </div>
  );
}
