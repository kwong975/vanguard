/**
 * SessionStart Hook: Display Active Projects
 *
 * Shows a compact project summary as a systemMessage so __USER_NAME__ sees it
 * in the terminal at session start.
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
const cwd = resolve(process.cwd());

interface Project {
  name: string;
  status: string;
  nextItems: string[];
}

function loadProjects(): Project[] {
  if (!existsSync(projectsDir)) return [];
  const currentWorkspace = detectWorkspace(cwd, devDir);
  const files = readdirSync(projectsDir)
    .filter((f) => f.endsWith(".md") && !f.endsWith("-notes.md"))
    .filter((f) => statSync(join(projectsDir, f)).isFile())
    .sort();

  const projects: Project[] = [];
  for (const file of files) {
    const raw = readFileSync(join(projectsDir, file), "utf-8").trim();
    const { workspace, status, body } = parseFrontmatter(raw);
    if (!shouldLoad(workspace, status, currentWorkspace)) continue;
    const name = extractTitle(body) || file.replace(/\.md$/, "");
    const nextItems = extractNextItems(body);
    projects.push({ name, status, nextItems });
  }
  return projects;
}

const projects = loadProjects();
if (projects.length > 0) {
  const lines = [`Active Projects (${projects.length}):`];
  for (const p of projects) {
    lines.push(`  ${p.name}`);
    for (const item of p.nextItems) {
      lines.push(`    - ${item.slice(0, 70)}`);
    }
  }
  const output = JSON.stringify({ systemMessage: lines.join("\n") });
  process.stdout.write(output);
}
