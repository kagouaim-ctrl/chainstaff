import type { ReactNode } from "react";
import { AVATAR_COLORS, AVATAR_SHAPES, type Appearance, type AvatarColor, type AvatarShape } from "../lib/at";

/**
 * KAGOU's own geometric avatars: eight marks drawn on a 24-unit grid, six restrained colours. A worker keeps the
 * shape and colour picked when it was created (saved on the server as `appearance`); one created before that
 * gets a stable pair derived from its id, so it looks the same on every page and every visit.
 */

export const COLOR_HEX: Record<AvatarColor, string> = {
  gold: "#F0B90B",
  amber: "#E8954A",
  emerald: "#19C37D",
  azure: "#4A9EFF",
  coral: "#F0706A",
  slate: "#9AA4B5",
};

export const SHAPE_LABEL: Record<AvatarShape, string> = {
  hex: "Hex", orbit: "Orbit", prism: "Prism", grid: "Grid", pulse: "Pulse", shield: "Shield", node: "Node", spark: "Spark",
};

const SW = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;

const SHAPES: Record<AvatarShape, ReactNode> = {
  hex: (
    <>
      <path d="M12 3.2 19.6 7.6v8.8L12 20.8 4.4 16.4V7.6z" {...SW} />
      <circle cx="12" cy="12" r="2.6" fill="currentColor" />
    </>
  ),
  orbit: (
    <>
      <circle cx="12" cy="12" r="3.4" fill="currentColor" />
      <ellipse cx="12" cy="12" rx="8.6" ry="4.2" transform="rotate(-28 12 12)" {...SW} />
    </>
  ),
  prism: (
    <>
      <path d="M12 3.6 20.2 18.4H3.8z" {...SW} />
      <path d="M12 3.6v14.8" {...SW} opacity="0.55" />
    </>
  ),
  grid: (
    <>
      <rect x="4.2" y="4.2" width="6.6" height="6.6" rx="2" fill="currentColor" />
      <rect x="13.2" y="4.2" width="6.6" height="6.6" rx="2" {...SW} />
      <rect x="4.2" y="13.2" width="6.6" height="6.6" rx="2" {...SW} />
      <rect x="13.2" y="13.2" width="6.6" height="6.6" rx="2" fill="currentColor" opacity="0.55" />
    </>
  ),
  pulse: (
    <>
      <circle cx="12" cy="12" r="2.4" fill="currentColor" />
      <circle cx="12" cy="12" r="5.4" {...SW} opacity="0.75" />
      <circle cx="12" cy="12" r="8.6" {...SW} opacity="0.4" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3.4 19 6v5.6c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6z" {...SW} />
      <path d="m8.8 12 2.2 2.2 4.2-4.4" {...SW} />
    </>
  ),
  node: (
    <>
      <path d="M7 7.4 12 16.6 17 7.4z" {...SW} opacity="0.55" />
      <circle cx="7" cy="7.4" r="2.6" fill="currentColor" />
      <circle cx="17" cy="7.4" r="2.6" fill="currentColor" />
      <circle cx="12" cy="16.6" r="2.6" fill="currentColor" />
    </>
  ),
  spark: (
    <>
      <path d="M12 3.4c.6 4.6 2.2 6.4 6.8 7-4.6.6-6.2 2.4-6.8 7-.6-4.6-2.2-6.4-6.8-7 4.6-.6 6.2-2.4 6.8-7z" fill="currentColor" />
      <circle cx="18.6" cy="18.4" r="1.5" fill="currentColor" opacity="0.6" />
    </>
  ),
};

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** The saved appearance, completed with a stable default derived from the id. */
export function resolveAppearance(id: string, a?: Appearance | null): { shape: AvatarShape; color: AvatarColor } {
  const h = hash(id || "worker");
  return {
    shape: a?.shape && SHAPES[a.shape] ? a.shape : AVATAR_SHAPES[h % AVATAR_SHAPES.length],
    color: a?.color && COLOR_HEX[a.color] ? a.color : AVATAR_COLORS[(h >>> 8) % AVATAR_COLORS.length],
  };
}

export function ShapeMark({ shape, size = 20 }: { shape: AvatarShape; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      {SHAPES[shape]}
    </svg>
  );
}

/** Rounded tile with the mark; the tile is the colour at low opacity, the mark at full. */
export function WorkerAvatar({ id, appearance, size = 40, title, dim }: { id: string; appearance?: Appearance | null; size?: number; title?: string; dim?: boolean }) {
  const { shape, color } = resolveAppearance(id, appearance);
  const hex = COLOR_HEX[color];
  return (
    <span
      className={`wavatar ${dim ? "dim" : ""}`}
      title={title}
      style={{
        width: size, height: size, color: hex,
        borderRadius: Math.max(6, Math.round(size * 0.35)),
        background: `color-mix(in srgb, ${hex} 13%, var(--bg-card))`,
        boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${hex} 22%, transparent)`,
      }}
    >
      <ShapeMark shape={shape} size={Math.round(size * 0.58)} />
    </span>
  );
}
