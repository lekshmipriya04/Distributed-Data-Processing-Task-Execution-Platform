// Finds the root error in a task's output for any language. Pure (no React)
// so it is unit-tested against real compiler and interpreter output.

export interface KeyError {
  message: string;
  file?: string;
  line?: number;
  column?: number;
  kind: 'compile' | 'runtime' | 'timeout' | 'exit';
}

// eslint-disable-next-line no-control-regex
const ANSI = /\u001b\[[0-9;?]*[ -/]*[@-~]|\u001b\][^\u0007]*\u0007/g;

export function stripAnsi(text: string): string {
  return text.replace(ANSI, '');
}

const FILE = String.raw`((?:[A-Za-z]:)?[\w.\-/\\]+\.[A-Za-z0-9]+)`;

// gcc / clang / javac / swiftc / tsc-style: path:line[:col]: error: message
const COLON_ERROR = new RegExp(`${FILE}:(\\d+)(?::(\\d+))?:\\s*(?:fatal\\s+)?error:\\s*(.+)`, 'i');
// Go: ./main.go:5:2: undefined: x   (no "error" keyword)
const GO_ERROR = new RegExp(`(\\S+\\.go):(\\d+):(\\d+):\\s*(.+)`);
// Rust: "error[E0308]: mismatched types" then "  --> src/main.rs:3:5"
const RUST_ERROR = /^error(?:\[E\d+\])?:\s*(.+)$/m;
const RUST_AT = /-->\s*(\S+?):(\d+):(\d+)/;
// Python traceback frames: File "x.py", line 3, in <module>
const PY_FRAME = /File "([^"]+)", line (\d+)/g;
// A final "SomethingError: message" / "Exception: message" line.
const EXCEPTION_LINE = /^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)):?\s*(.*)$/;
// JavaScript stack frame: at fn (/path/file.js:3:9) or at /path/file.js:3:9
const JS_FRAME = new RegExp(`\\(?${FILE}:(\\d+):(\\d+)\\)?`);
// Ruby: t.rb:2:in 'Object#f': undefined method 'upcase' for nil (NoMethodError)
const RUBY_ERROR = /^(\S+\.rb):(\d+):in [^:]+: (.+?) \(([A-Z]\w*)\)$/m;
const TIMED_OUT = /\[task timed out\]|timed out after/i;

function lastNonEmpty(lines: string[]): string | undefined {
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].trim() !== '') {
      return lines[i].trim();
    }
  }
  return undefined;
}

/**
 * The most relevant error in ``stderr`` (falling back to ``stdout``), or null
 * when the task succeeded and printed nothing alarming.
 */
export function extractKeyError(stderr?: string | null, exitCode?: number | null, stdout?: string | null): KeyError | null {
  const err = stripAnsi(stderr ?? '');
  const out = stripAnsi(stdout ?? '');
  const failed = exitCode !== undefined && exitCode !== null && exitCode !== 0;
  if (!err.trim() && !failed) {
    return null;
  }
  if (exitCode === 124 || TIMED_OUT.test(err)) {
    return { kind: 'timeout', message: 'The task hit its time limit and was stopped.' };
  }

  const colon = COLON_ERROR.exec(err);
  if (colon) {
    return {
      kind: 'compile',
      file: colon[1],
      line: Number(colon[2]),
      column: colon[3] ? Number(colon[3]) : undefined,
      message: colon[4].trim(),
    };
  }

  const rust = RUST_ERROR.exec(err);
  if (rust) {
    const at = RUST_AT.exec(err.slice(rust.index));
    return {
      kind: 'compile',
      message: rust[1].trim(),
      file: at?.[1],
      line: at ? Number(at[2]) : undefined,
      column: at ? Number(at[3]) : undefined,
    };
  }

  const go = GO_ERROR.exec(err);
  if (go) {
    return { kind: 'compile', file: go[1], line: Number(go[2]), column: Number(go[3]), message: go[4].trim() };
  }

  const ruby = RUBY_ERROR.exec(err);
  if (ruby) {
    return { kind: 'runtime', file: ruby[1], line: Number(ruby[2]), message: `${ruby[4]}: ${ruby[3]}` };
  }

  const lines = err.split(/\r?\n/);
  const exceptionLine = [...lines].reverse().find((l) => EXCEPTION_LINE.test(l.trim()));
  if (exceptionLine) {
    const frames = [...err.matchAll(PY_FRAME)];
    const frame = frames[frames.length - 1];
    if (frame) {
      return { kind: 'runtime', message: exceptionLine.trim(), file: frame[1], line: Number(frame[2]) };
    }
    const js = JS_FRAME.exec(err.slice(err.indexOf(exceptionLine)));
    return {
      kind: 'runtime',
      message: exceptionLine.trim(),
      file: js?.[1],
      line: js ? Number(js[2]) : undefined,
      column: js ? Number(js[3]) : undefined,
    };
  }

  const fallback = lastNonEmpty(lines) ?? (failed ? lastNonEmpty(out.split(/\r?\n/)) : undefined);
  if (!failed && !/error|fatal|exception|panic/i.test(err)) {
    // Something was written to stderr, but the task succeeded: not an error.
    return null;
  }
  return { kind: 'exit', message: fallback ?? `The task exited with code ${exitCode}.` };
}

/** Lines of ``text`` containing ``query`` (case-insensitive), ANSI kept intact. */
export function filterLines(text: string, query: string): string[] {
  const q = query.trim().toLowerCase();
  const lines = text.split(/\r?\n/);
  return q ? lines.filter((l) => stripAnsi(l).toLowerCase().includes(q)) : lines;
}
