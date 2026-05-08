import type { Category } from "../api/types";

// Mirrors CATEGORY_COLORS in the legacy app.js. Keep in sync with
// dashboard/api.py's _LAYER_TO_CATEGORY mapping when new layers land.
export const CATEGORY_COLORS: Record<string, string> = {
  iac_files: "#1677ff",
  tg_state: "#ff9900",
  k8s_resources: "#ff5552",
  docs: "#9da3ad",
  cue: "#a259ff",
  ci_cd: "#3ddc84",
  applications: "#ff4f9c",
  live_observability: "#f5b800",
  aws: "#ff9900",
  dependency: "#c0c4cc",
  meta: "#ffffff",
};

export const CATEGORY_LABELS: Record<string, string> = {
  iac_files: "IaC files",
  tg_state: "TG / OpenTofu state",
  k8s_resources: "K8s resources",
  docs: "Docs",
  cue: "CUE schemas",
  ci_cd: "CI/CD workflows",
  applications: "Applications",
  live_observability: "Live observability",
  aws: "AWS resources",
  dependency: "Dependencies",
  meta: "Meta layer",
};

export const ALL_CATEGORIES: Category[] = [
  "iac_files",
  "tg_state",
  "k8s_resources",
  "docs",
  "cue",
  "ci_cd",
  "applications",
  "live_observability",
  "aws",
  "dependency",
  "meta",
];

// AWS-services category → tile colour (used in Dashboard arch grid).
export const AWS_CAT_COLORS: Record<string, string> = {
  "Edge/CDN": "#a259ff",
  Compute: "#1677ff",
  Storage: "#3ddc84",
  Database: "#3ddc84",
  Network: "#ff9900",
  "Security/IAM": "#f5b800",
  Monitoring: "#9da3ad",
  "Lambda/Serverless": "#ff4f9c",
  Other: "#c0c4cc",
};

// Map AWS scanner type → emoji-ish glyph, kept ASCII-safe so it copies.
// The legacy frontend used per-tile SVG icons; for the MVP we go with a
// subtle two-letter mark per service so the grid still parses at a glance.
export function awsTypeMark(nodeType: string): string {
  const t = nodeType.replace(/^aws_/, "");
  return t.slice(0, 2).toUpperCase();
}
