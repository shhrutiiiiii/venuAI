"use client";

import React, { useMemo } from "react";

export interface GraphEntity {
  entity_id: string;
  entity_type: string;
  name: string;
  classification: string;
}

export interface GraphRelationship {
  relationship_id: string;
  source_entity_id: string;
  target_entity_id: string;
  relationship_type: string;
  valid_from?: string | null;
  valid_until?: string | null;
}

interface Props {
  center: GraphEntity;
  relationships: GraphRelationship[];
  relatedEntities: GraphEntity[];
  onNodeClick: (entityId: string) => void;
}

const TYPE_COLORS: Record<string, string> = {
  document: "#6366f1",
  organization: "#06b6d4",
  researcher: "#ec4899",
  technology: "#f59e0b",
  system: "#f59e0b",
  project: "#10b981",
  laboratory: "#06b6d4",
  dataset: "#8b5cf6",
  model: "#8b5cf6",
  paper: "#6366f1",
  event: "#f43f5e",
  location: "#64748b",
  concept: "#64748b",
};

const SIZE = 340;
const CENTER = SIZE / 2;
const RADIUS = 125;
const NODE_R = 26;
const CENTER_NODE_R = 32;

/**
 * Real node-link graph visualization — a center entity plus its directly
 * connected neighbors arranged in a circle, edges labeled by relationship
 * type. Pure inline SVG, no external graph/charting library: the node
 * count per view is small (one entity's immediate neighborhood, not the
 * whole graph), so a radial layout is enough to read clearly — see
 * Documents/AI/knowledge-graph.md "Graph Visualization UI".
 */
export default function GraphVisualization({ center, relationships, relatedEntities, onNodeClick }: Props) {
  const byId = useMemo(() => {
    const m = new Map<string, GraphEntity>();
    for (const e of relatedEntities) m.set(e.entity_id, e);
    m.set(center.entity_id, center);
    return m;
  }, [center, relatedEntities]);

  const neighborIds = useMemo(() => {
    const ids = new Set<string>();
    for (const r of relationships) {
      if (r.source_entity_id !== center.entity_id) ids.add(r.source_entity_id);
      if (r.target_entity_id !== center.entity_id) ids.add(r.target_entity_id);
    }
    return Array.from(ids);
  }, [relationships, center.entity_id]);

  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    map.set(center.entity_id, { x: CENTER, y: CENTER });
    neighborIds.forEach((id, i) => {
      const angle = (i / Math.max(neighborIds.length, 1)) * 2 * Math.PI - Math.PI / 2;
      map.set(id, {
        x: CENTER + RADIUS * Math.cos(angle),
        y: CENTER + RADIUS * Math.sin(angle),
      });
    });
    return map;
  }, [neighborIds, center.entity_id]);

  if (neighborIds.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-xs italic" style={{ color: "var(--text-subtle)" }}>
        No relationships to visualize yet.
      </div>
    );
  }

  return (
    <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="w-full h-auto max-h-[380px]" role="img" aria-label={`Knowledge graph neighborhood of ${center.name}`}>
      {relationships.map((r) => {
        const otherId = r.source_entity_id === center.entity_id ? r.target_entity_id : r.source_entity_id;
        const p1 = positions.get(center.entity_id)!;
        const p2 = positions.get(otherId);
        if (!p2) return null;
        const midX = (p1.x + p2.x) / 2;
        const midY = (p1.y + p2.y) / 2;
        return (
          <g key={r.relationship_id}>
            <line x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y} stroke="var(--border-subtle)" strokeWidth={1.5} />
            <text x={midX} y={midY - 4} textAnchor="middle" fontSize={8} fill="var(--text-subtle)" style={{ paintOrder: "stroke", stroke: "var(--bg-card)", strokeWidth: 3 }}>
              {r.relationship_type}
            </text>
          </g>
        );
      })}

      {neighborIds.map((id) => {
        const entity = byId.get(id);
        const pos = positions.get(id);
        if (!entity || !pos) return null;
        const color = TYPE_COLORS[entity.entity_type] || "#64748b";
        return (
          <g key={id} onClick={() => onNodeClick(id)} className="cursor-pointer" style={{ cursor: "pointer" }}>
            <circle cx={pos.x} cy={pos.y} r={NODE_R} fill={color} opacity={0.85} />
            <text x={pos.x} y={pos.y + 1} textAnchor="middle" dominantBaseline="middle" fontSize={8} fontWeight={700} fill="#fff">
              {entity.name.slice(0, 10)}
            </text>
            <text x={pos.x} y={pos.y + NODE_R + 11} textAnchor="middle" fontSize={7} fill="var(--text-subtle)">
              {entity.entity_type}
            </text>
          </g>
        );
      })}

      <circle cx={CENTER} cy={CENTER} r={CENTER_NODE_R} fill={TYPE_COLORS[center.entity_type] || "#64748b"} stroke="var(--bg-card)" strokeWidth={3} />
      <text x={CENTER} y={CENTER + 1} textAnchor="middle" dominantBaseline="middle" fontSize={9} fontWeight={800} fill="#fff">
        {center.name.slice(0, 12)}
      </text>
    </svg>
  );
}
