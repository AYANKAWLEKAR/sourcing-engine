"use client";

import { motion } from "framer-motion";

interface GaugeProps {
  value: number; // 0..1
  label: string;
  display?: string; // override centre text
  size?: number;
  tone?: "accent" | "good" | "warn" | "muted";
}

const TONES: Record<string, string> = {
  accent: "#f26d5b",
  good: "#3fb98a",
  warn: "#e0b64a",
  muted: "#6b7488",
};

export function Gauge({ value, label, display, size = 56, tone = "accent" }: GaugeProps) {
  const v = Math.max(0, Math.min(1, value || 0));
  const stroke = 5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const color = TONES[tone] || TONES.accent;

  return (
    <div className="flex flex-col items-center gap-1" title={`${label}: ${(v * 100).toFixed(0)}%`}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#252a3a" strokeWidth={stroke} />
          <motion.circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={c}
            initial={{ strokeDashoffset: c }}
            animate={{ strokeDashoffset: c * (1 - v) }}
            transition={{ duration: 0.7, ease: "easeOut" }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center text-[11px] font-semibold text-slate-100">
          {display ?? (v * 100).toFixed(0)}
        </div>
      </div>
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
    </div>
  );
}
