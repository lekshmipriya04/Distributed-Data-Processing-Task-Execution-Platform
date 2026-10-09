// Owns the connection lifecycle: holds the configured master URL, the token
// provider (SecretStorage-backed), the API client, and a small state machine.
// Views and the status bar subscribe to `onDidChangeState` rather than polling
// this object.

import * as vscode from 'vscode';
import { ApiClient } from '../api/client';
import { GatewayApi } from '../api/gateway';
import { validateMasterUrl } from '../api/urls';
import { ApiError } from '../models/http';
import { readSettings } from '../configuration/settings';
import { TokenStore } from '../security/secretStorage';
import { Logger } from '../utilities/logger';

export type ConnectionState = 'disconnected' | 'connecting' | 'connected';

export interface ConnectionSnapshot {
  state: ConnectionState;
  masterUrl: string;
  /** Last error message (secret-free) when a connect attempt failed. */
  lastError?: string;
}

export class ConnectionManager implements vscode.Disposable {
  private state: ConnectionState = 'disconnected';
  private masterUrl = '';
  private lastError: string | undefined;
  private api: GatewayApi | undefined;

  private readonly emitter = new vscode.EventEmitter<ConnectionSnapshot>();
  readonly onDidChangeState = this.emitter.event;

  constructor(
    private readonly tokens: TokenStore,
    private readonly logger: Logger,
  ) {}

  snapshot(): ConnectionSnapshot {
    return { state: this.state, masterUrl: this.masterUrl, lastError: this.lastError };
  }

  /** The gateway client; defined only while connected. */
  gateway(): GatewayApi | undefined {
    return this.state === 'connected' ? this.api : undefined;
  }

  private setState(state: ConnectionState, lastError?: string): void {
    this.state = state;
    this.lastError = lastError;
    this.emitter.fire(this.snapshot());
  }

  /**
   * Validate the configured URL, build a client, and verify reachability with a
   * health check. Does not throw — returns true on success and reports failures
   * through state + an actionable message.
   */
  async connect(): Promise<boolean> {
    const { masterUrl } = readSettings();
    const validation = validateMasterUrl(masterUrl);
    if (!validation.ok) {
      this.masterUrl = masterUrl;
      this.setState('disconnected', validation.reason);
      this.logger.warn('connect aborted: invalid master URL', { reason: validation.reason });
      return false;
    }

    this.masterUrl = masterUrl;
    this.setState('connecting');

    const client = new ApiClient({
      baseUrl: masterUrl,
      getToken: () => this.tokens.get(),
    });
    const api = new GatewayApi(client);

    try {
      await api.health();
      this.api = api;
      this.setState('connected');
      this.logger.info('connected to master');
      return true;
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Failed to reach the master.';
      this.api = undefined;
      this.setState('disconnected', message);
      this.logger.warn('connect failed', { message });
      return false;
    }
  }

  disconnect(): void {
    this.api = undefined;
    this.setState('disconnected');
    this.logger.info('disconnected from master');
  }

  dispose(): void {
    this.emitter.dispose();
  }
}
