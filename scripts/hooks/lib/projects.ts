/**
 * Shared project parsing utilities.
 *
 * Used by LoadProjects and ShowProjects hooks.
 */

export interface ProjectFrontmatter {
  workspace: string;
  status: string;
  body: string;
}

export function parseFrontmatter(content: string): ProjectFrontmatter {
  const match = content.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!match) return { workspace: "dev", status: "active", body: content };

  const fm = match[1];
  const body = match[2].trim();

  const workspace = (fm.match(/^workspace:\s*(.+)$/m)?.[1] ?? "dev").trim();
  const status = (fm.match(/^status:\s*(.+)$/m)?.[1] ?? "active").trim();

  return { workspace, status, body };
}

export function shouldLoad(
  workspace: string,
  status: string,
  currentWorkspace: string,
): boolean {
  if (status !== "active") return false;
  if (workspace === "all") return true;
  if (workspace === "dev") return true;
  if (currentWorkspace === "dev") return true;
  return workspace === currentWorkspace;
}

export function extractTitle(body: string): string {
  const m = body.match(/^# (?:Project:\s*)?(.+)$/m);
  return m ? m[1].trim() : "";
}

export function extractNextItems(body: string): string[] {
  const nextMatch = body.match(/^## Next\n([\s\S]*?)(?=\n## |\n---|\Z)/m);
  if (!nextMatch) return [];
  return nextMatch[1]
    .split("\n")
    .filter((l) => l.match(/^- \[ \]/))
    .map((l) => l.replace(/^- \[ \]\s*/, "").trim())
    .filter(Boolean)
    .slice(0, 3);
}

export function detectWorkspace(cwd: string, devDir: string): string {
  if (cwd.startsWith(devDir)) {
    const relative = cwd.slice(devDir.length + 1);
    const firstSegment = relative.split("/")[0];
    if (firstSegment) return firstSegment;
  }
  return "dev";
}
