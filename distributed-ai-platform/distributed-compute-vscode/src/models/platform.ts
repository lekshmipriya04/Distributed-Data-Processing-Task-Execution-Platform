// Pure decisions for running the platform on this machine with Docker, kept
// free of the vscode API so they can be unit tested.

/** Where the extension downloads the platform when the user has no copy. */
export const PLATFORM_REPO = 'https://github.com/lekshmipriya04/Distributed-Data-Processing-Task-Execution-Platform.git';

/** The platform's docker-compose.yml lives in this folder of the repository. */
export const PLATFORM_SUBDIR = 'distributed-ai-platform';

export type DockerState = 'running' | 'stopped' | 'missing';

export type StartupAction =
  /** The local gateway answers: connect to it. */
  | 'connect'
  /** The platform was set up before but is not running: start it. */
  | 'start'
  /** Never set up here: offer the one-click setup. */
  | 'offer-setup'
  /** Remote master, or the user said no: do what the settings say. */
  | 'none';

export interface StartupFacts {
  masterIsLocal: boolean;
  gatewayHealthy: boolean;
  /** A platform folder is known (set up before, or open in the workspace). */
  platformKnown: boolean;
  autoStart: boolean;
  setupDismissed: boolean;
}

export function startupAction(f: StartupFacts): StartupAction {
  if (!f.masterIsLocal) {
    return 'none';
  }
  if (f.gatewayHealthy) {
    return 'connect';
  }
  if (f.platformKnown) {
    return f.autoStart ? 'start' : 'none';
  }
  return f.setupDismissed ? 'none' : 'offer-setup';
}

/** Read the outcome of `docker info`: spawn failure means no Docker CLI. */
export function dockerStateFrom(result: { spawnFailed: boolean; exitCode: number }): DockerState {
  if (result.spawnFailed) {
    return 'missing';
  }
  return result.exitCode === 0 ? 'running' : 'stopped';
}

/** How to start Docker Desktop on this OS, or undefined when we cannot. */
export function dockerLaunchCommand(platform: NodeJS.Platform): { cmd: string; args: string[] } | undefined {
  switch (platform) {
    case 'darwin':
      return { cmd: 'open', args: ['-a', 'Docker'] };
    case 'win32':
      return { cmd: 'cmd', args: ['/c', 'start', '""', 'C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe'] };
    case 'linux':
      return { cmd: 'systemctl', args: ['--user', 'start', 'docker-desktop'] };
    default:
      return undefined;
  }
}

export const DOCKER_DOWNLOAD_URL = 'https://www.docker.com/products/docker-desktop/';
