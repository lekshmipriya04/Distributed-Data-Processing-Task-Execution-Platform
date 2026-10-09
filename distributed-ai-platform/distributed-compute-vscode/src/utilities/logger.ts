// Diagnostics logger over a VS Code OutputChannel. Honors the configured log
// level and routes every message through the secret redaction helper so a token
// can never land in the channel, even if a caller passes one by mistake.

import * as vscode from 'vscode';
import type { LogLevel } from '../configuration/settings';
import { redact, redactString } from '../security/redaction';

const ORDER: Record<LogLevel, number> = { error: 0, warn: 1, info: 2, debug: 3 };

export class Logger {
  private level: LogLevel = 'info';

  constructor(private readonly channel: vscode.OutputChannel) {}

  setLevel(level: LogLevel): void {
    this.level = level;
  }

  show(): void {
    this.channel.show(true);
  }

  error(message: string, meta?: unknown): void {
    this.write('error', message, meta);
  }
  warn(message: string, meta?: unknown): void {
    this.write('warn', message, meta);
  }
  info(message: string, meta?: unknown): void {
    this.write('info', message, meta);
  }
  debug(message: string, meta?: unknown): void {
    this.write('debug', message, meta);
  }

  private write(level: LogLevel, message: string, meta?: unknown): void {
    if (ORDER[level] > ORDER[this.level]) {
      return;
    }
    const ts = new Date().toISOString();
    let line = `[${ts}] [${level}] ${redactString(message)}`;
    if (meta !== undefined) {
      try {
        line += ' ' + JSON.stringify(redact(meta));
      } catch {
        line += ' [unserializable meta]';
      }
    }
    this.channel.appendLine(line);
  }
}
