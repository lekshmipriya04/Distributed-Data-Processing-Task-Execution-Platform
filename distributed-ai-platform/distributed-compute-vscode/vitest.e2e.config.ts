import { defineConfig } from 'vitest/config';

// End-to-end tests against a running platform; see e2e/platform.e2e.test.ts.
export default defineConfig({
  test: {
    include: ['e2e/**/*.e2e.test.ts'],
    environment: 'node',
    fileParallelism: false,
    testTimeout: 120_000,
    hookTimeout: 120_000,
  },
});
