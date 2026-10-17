import { describe, expect, it } from 'vitest';
import { dockerLaunchCommand, dockerStateFrom, startupAction, type StartupFacts } from '../models/platform';

const base: StartupFacts = {
  masterIsLocal: true,
  gatewayHealthy: false,
  platformKnown: false,
  autoStart: true,
  setupDismissed: false,
};

describe('startupAction', () => {
  it('leaves a remote master to the autoConnect setting', () => {
    expect(startupAction({ ...base, masterIsLocal: false, gatewayHealthy: true })).toBe('none');
  });

  it('connects when the local gateway already answers', () => {
    expect(startupAction({ ...base, gatewayHealthy: true })).toBe('connect');
  });

  it('starts a platform that was set up before', () => {
    expect(startupAction({ ...base, platformKnown: true })).toBe('start');
  });

  it('does not start containers when auto start is off', () => {
    expect(startupAction({ ...base, platformKnown: true, autoStart: false })).toBe('none');
  });

  it('offers setup on a fresh install, unless dismissed', () => {
    expect(startupAction(base)).toBe('offer-setup');
    expect(startupAction({ ...base, setupDismissed: true })).toBe('none');
  });
});

describe('docker helpers', () => {
  it('reads docker info outcomes', () => {
    expect(dockerStateFrom({ spawnFailed: true, exitCode: -1 })).toBe('missing');
    expect(dockerStateFrom({ spawnFailed: false, exitCode: 0 })).toBe('running');
    expect(dockerStateFrom({ spawnFailed: false, exitCode: 1 })).toBe('stopped');
  });

  it('knows how to launch Docker Desktop per OS', () => {
    expect(dockerLaunchCommand('darwin')).toEqual({ cmd: 'open', args: ['-a', 'Docker'] });
    expect(dockerLaunchCommand('win32')?.cmd).toBe('cmd');
    expect(dockerLaunchCommand('linux')?.cmd).toBe('systemctl');
    expect(dockerLaunchCommand('aix')).toBeUndefined();
  });
});
