// Bearer token persistence. The token lives ONLY in VS Code SecretStorage —
// never in settings, workspace state, logs, or webviews.

import * as vscode from 'vscode';

const TOKEN_KEY = 'distributedCompute.authToken';

export class TokenStore {
  constructor(private readonly secrets: vscode.SecretStorage) {}

  get(): Promise<string | undefined> {
    return Promise.resolve(this.secrets.get(TOKEN_KEY));
  }

  async set(token: string): Promise<void> {
    await this.secrets.store(TOKEN_KEY, token);
  }

  async clear(): Promise<void> {
    await this.secrets.delete(TOKEN_KEY);
  }

  async has(): Promise<boolean> {
    return (await this.secrets.get(TOKEN_KEY)) !== undefined;
  }
}

export { TOKEN_KEY };
