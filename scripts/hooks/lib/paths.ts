import { homedir } from "os";
import { join } from "path";

export function expandPath(p: string): string {
  const home = homedir();
  return p
    .replace(/^\$HOME(?=\/|$)/, home)
    .replace(/^\$\{HOME\}(?=\/|$)/, home)
    .replace(/^~(?=\/|$)/, home);
}

export function getDevDir(): string {
  const env = process.env.DEV_DIR;
  if (env) return expandPath(env);
  return join(homedir(), "dev");
}

export function getMemoryDir(): string {
  const env = process.env.MEMORY_DIR;
  if (env) return expandPath(env);
  // Default memory location when MEMORY_DIR is unset.
  
  
  return join(homedir(), ".claude", "vanguard-memory");
}
