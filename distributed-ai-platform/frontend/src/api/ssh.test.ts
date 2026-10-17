import { describe, expect, it } from 'vitest';
import { shortVersion } from './ssh';

describe('shortVersion', () => {
  it('keeps only the version number of a --version line', () => {
    expect(shortVersion('Python', 'Python 3.14.8')).toBe('3.14.8');
    expect(shortVersion('Bash', 'GNU bash, version 5.2.21(1)-release (x86_64-pc-linux-gnu)')).toBe('5.2.21');
    expect(shortVersion('Node.js', 'v20.11.1')).toBe('20.11.1');
  });

  it('drops a version that only repeats the name', () => {
    expect(shortVersion('POSIX Shell', 'shell')).toBe('');
    expect(shortVersion('Python', '')).toBe('');
    expect(shortVersion('Python', null)).toBe('');
  });

  it('keeps other text', () => {
    expect(shortVersion('Rust', 'nightly')).toBe('nightly');
  });
});
