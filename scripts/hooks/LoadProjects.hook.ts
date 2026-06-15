/**
 * SessionStart Hook: Load Context Summary
 *
 * Injects a lean startup payload:
 * - The canonical identity, read from the rendered projections/USER.md
 *   (+ MEMORY.md). an upstream tool may render one canonical
 *   identity into the memory dir's projections/ folder; the hook reads it instead of
 *   regex-scraping the raw MEMORIES.md. Degrades to the raw scrape when
 *   projections/USER.md is absent (pre-first-render / sync wedge) — never
 *   emits nothing.
 * - Active project summaries (name + next 3 items)
 * - Reference pointers
 *
 * Budget: <5KB total. Logs byte count to stderr for monitoring.
 */

import { readdirSync, readFileSync, existsSync, statSync } from "fs";
import { join, resolve } from "path";
import { getDevDir, getMemoryDir } from "./lib/paths";
import {
  parseFrontmatter,
  shouldLoad,
  extractTitle,
  extractNextItems,
  detectWorkspace,
} from "./lib/projects";

const devDir = getDevDir();
const memoryDir = getMemoryDir();
const projectsDir = join(memoryDir, "projects");
const memoriesFile = join(memoryDir, "memories", "MEMORIES.md");
const projectionsDir = join(memoryDir, "projections");
const userProjection = join(projectionsDir, "USER.md");
const memoryProjection = join(projectionsDir, "MEMORY.md");
const cwd = resolve(process.cwd());

// Two-section marker an upstream renderer may use: The
// canonical identity is rendered ABOVE this line, with local notes below
// it (the surface's own local notes). The hook injects only the rendered section,
// so it must split on this exact string. Keep byte-identical with the renderer.
const LOCAL_MARKER =
  "<!-- ▼ local notes — preserved across renders; local notes below this line ▼ -->";

// The rendered section of a projection file: everything ABOVE the local marker,
// with the leading `<!-- projection | rendered: ... -->` header
// comment stripped (it is render metadata, not identity content).
function renderedSection(content: string): string {
  const markerIdx = content.indexOf(LOCAL_MARKER);
  const rendered = markerIdx === -1 ? content : content.slice(0, markerIdx);
  return rendered
    .replace(/^<!-- projection \| rendered:[\s\S]*?-->\n?/, "")
    .trim();
}

// Safety net: skip any project body over this threshold even if frontmatter says active
const MAX_PROJECT_BODY_BYTES = 5120; // 5KB

function loadProjectSummaries(): string {
  if (!existsSync(projectsDir)) return "";

  const currentWorkspace = detectWorkspace(cwd, devDir);

  const files = readdirSync(projectsDir)
    .filter((f) => f.endsWith(".md") && !f.endsWith("-notes.md"))
    .filter((f) => statSync(join(projectsDir, f)).isFile())
    .sort();

  const summaries: string[] = [];

  for (const file of files) {
    const filePath = join(projectsDir, file);
    const raw = readFileSync(filePath, "utf-8").trim();
    const { workspace, status, body } = parseFrontmatter(raw);

    if (!shouldLoad(workspace, status, currentWorkspace)) continue;

    const bodyBytes = Buffer.byteLength(body, "utf-8");
    if (bodyBytes > MAX_PROJECT_BODY_BYTES) {
      console.error(
        `[LoadProjects] WARN ${file} — body ${bodyBytes} bytes (extracting summary only)`,
      );
    }

    const name = extractTitle(body) || file.replace(/\.md$/, "");
    const nextItems = extractNextItems(body);

    let summary = `- ${name}`;
    for (const item of nextItems) {
      summary += `\n  → ${item.slice(0, 80)}`;
    }
    summary += `\n  📎 ${memoryDir}/projects/${file}`;

    summaries.push(summary);
  }

  if (summaries.length === 0) return "";

  return `ACTIVE PROJECTS (${summaries.length}):\n\n${summaries.join("\n\n")}`;
}

// read the rendered canonical identity (projections/USER.md +
// MEMORY.md), rendered section only. Returns "" when projections/USER.md is
// absent, which signals the caller to DEGRADE to the raw scrape.
function loadIdentity(): string {
  if (!existsSync(userProjection)) return "";

  const blocks: string[] = [];
  const userBrain = renderedSection(readFileSync(userProjection, "utf-8"));
  if (userBrain) blocks.push(userBrain);

  // MEMORY.md is the broader learned set — include its rendered section when
  // present (optional; USER.md is the identity headline).
  if (existsSync(memoryProjection)) {
    const memBrain = renderedSection(readFileSync(memoryProjection, "utf-8"));
    if (memBrain) blocks.push(memBrain);
  }

  if (blocks.length === 0) return "";
  return `IDENTITY (rendered):\n\n${blocks.join("\n\n")}`;
}

function loadCorrections(): string {
  if (!existsSync(memoriesFile)) return "";
  const content = readFileSync(memoriesFile, "utf-8");

  const sections: string[] = [];

  // Extract specific sections: Calibrations, Preferences, Corrections
  const sectionPatterns = [
    /^## Explicit Calibrations\n([\s\S]*?)(?=\n## |\Z)/m,
    /^## Stated Preferences\n([\s\S]*?)(?=\n## |\Z)/m,
    /^## Behavioral Corrections\n([\s\S]*?)(?=\n## |\Z)/m,
  ];

  for (const pattern of sectionPatterns) {
    const match = content.match(pattern);
    if (match) {
      const body = match[1]
        .split("\n")
        .filter((l) => l.trimStart().startsWith("- "))
        .join("\n");
      if (body.trim()) sections.push(body);
    }
  }

  if (sections.length === 0) return "";

  return `CORRECTIONS & CALIBRATIONS:\n\n${sections.join("\n")}`;
}

// Build payload. prefer the rendered identity; DEGRADE to the
// raw MEMORIES.md scrape when projections/USER.md is absent (so the hook
// never emits nothing). The degrade is VISIBLE (logged to stderr), not silent.
let identity = loadIdentity();
if (identity) {
  console.error("[LoadProjects] identity: rendered projections/USER.md");
} else {
  identity = loadCorrections();
  console.error(
    "[LoadProjects] identity: DEGRADED to raw MEMORIES.md scrape " +
      "(projections/USER.md absent — pre-first-render or sync wedge)",
  );
}
const projects = loadProjectSummaries();

const references = [
  `📎 Full memories: ${memoryDir}/memories/MEMORIES.md`,
  `📎 Known issues: ${memoryDir}/known-issues/`,
].join("\n");

const parts = [identity, projects, references].filter(Boolean);
const payload = parts.join("\n\n=====\n\n");

// Log byte count for budget monitoring
const byteCount = Buffer.byteLength(payload, "utf-8");
const kb = (byteCount / 1024).toFixed(1);
console.error(`[LoadProjects] payload: ${byteCount} bytes (${kb}KB)`);
if (byteCount > 5120) {
  console.error(`[LoadProjects] WARNING: payload exceeds 5KB budget (${kb}KB)`);
}

if (payload.trim()) process.stdout.write(payload);
