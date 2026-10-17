import { describe, expect, it } from 'vitest';
import { extractKeyError, filterLines, stripAnsi } from './logParsing';
import { CPPCOLOR_ERR, CPP_ERR, GO_ERR, JAVA_ERR, JS_ERR, PY_ERR, RB_ERR, RUST_ERR } from './__fixtures__/logs';

describe('extractKeyError on real compiler and interpreter output', () => {
  it('Python: last exception line and the innermost frame', () => {
    expect(extractKeyError(PY_ERR, 1)).toEqual({
      kind: 'runtime', message: 'ZeroDivisionError: division by zero', file: '/home/worker/sandbox/t.py', line: 2,
    });
  });
  it('C++ (g++/clang): file, line, column and message', () => {
    expect(extractKeyError(CPP_ERR, 1)).toEqual({
      kind: 'compile', file: 'm.cpp', line: 2, column: 12, message: "expected ';' at end of declaration",
    });
  });
  it('colored compiler output parses the same once ANSI is removed', () => {
    expect(CPPCOLOR_ERR).toContain('\u001b[');
    expect(extractKeyError(CPPCOLOR_ERR, 1)).toEqual(extractKeyError(CPP_ERR, 1));
    expect(stripAnsi(CPPCOLOR_ERR)).not.toContain('\u001b');
  });
  it('Node.js: the error line and the first frame after it', () => {
    expect(extractKeyError(JS_ERR, 1)).toEqual({
      kind: 'runtime', message: 'ReferenceError: missing is not defined',
      file: '/home/worker/sandbox/t.js', line: 2, column: 3,
    });
  });
  it('Java: javac errors have no column', () => {
    expect(extractKeyError(JAVA_ERR, 1)).toEqual({ kind: 'compile', file: 'Main.java', line: 3, message: "';' expected" });
  });
  it('Ruby: the class and message, not the last "from" line', () => {
    expect(extractKeyError(RB_ERR, 1)).toEqual({
      kind: 'runtime', file: 't.rb', line: 2, message: "NoMethodError: undefined method 'upcase' for nil",
    });
  });
  it('Go: errors without an "error:" keyword', () => {
    expect(extractKeyError(GO_ERR, 1)).toEqual({
      kind: 'compile', file: './main.go', line: 3, column: 2, message: 'declared and not used: x',
    });
  });
  it('Rust: the location comes from the --> line', () => {
    expect(extractKeyError(RUST_ERR, 1)).toEqual({
      kind: 'compile', file: 'main.rs', line: 2, column: 18, message: 'mismatched types',
    });
  });
});

describe('extractKeyError edge cases', () => {
  it('returns null for a clean success, even with harmless stderr', () => {
    expect(extractKeyError('', 0, 'Training complete')).toBeNull();
    expect(extractKeyError('downloading weights... 100%\n', 0)).toBeNull();
  });
  it('reports a timeout plainly', () => {
    expect(extractKeyError('[task timed out]', 124)).toMatchObject({ kind: 'timeout' });
  });
  it('falls back to the last stderr line, then stdout, on a bare non-zero exit', () => {
    expect(extractKeyError('something broke\n\n', 3)).toEqual({ kind: 'exit', message: 'something broke' });
    expect(extractKeyError('', 2, 'step 1\nusage: tool [-h]')).toEqual({ kind: 'exit', message: 'usage: tool [-h]' });
    expect(extractKeyError('', 5)).toEqual({ kind: 'exit', message: 'The task exited with code 5.' });
  });
});

describe('filterLines', () => {
  it('matches case-insensitively and ignores ANSI codes', () => {
    expect(filterLines(CPPCOLOR_ERR, 'ERROR').map(stripAnsi)).toEqual(["m.cpp:2:12: error: expected ';' at end of declaration", '1 error generated.']);
    expect(filterLines('a\nb\n', '')).toEqual(['a', 'b', '']);
  });
});
