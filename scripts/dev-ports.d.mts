export interface DevPorts {
  root: string;
  mainRoot: string;
  isWorktree: boolean;
  slot: number;
  frontendPort: number;
  backendPort: number;
  frontendUrl: string;
  backendOrigin: string;
}

export function getDevPorts(options?: { cwd?: string }): DevPorts;
export function writeLaunchJson(ports: DevPorts): string;
