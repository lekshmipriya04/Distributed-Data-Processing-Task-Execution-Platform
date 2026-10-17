import { defineConfig, devices } from '@playwright/test';

// Browser tests of the production dashboard. Start it first:
//   node e2e/serve.mjs        (nginx image on ports 4101-4103, see e2e/README)
// Each port is used as a different user. Tests share one backend, so they run
// one at a time.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  outputDir: 'e2e-results',
  use: {
    baseURL: process.env.DC_WEB_URL_LOCAL ?? 'http://127.0.0.1:4101',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    // The full flows (workers, runs, training, isolation) in one engine...
    { name: 'flows-chromium', testMatch: /flows\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    // ...and everything a browser can get wrong in every engine and form factor.
    { name: 'chromium', testMatch: /browsers\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', testMatch: /browsers\.spec\.ts/, use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', testMatch: /browsers\.spec\.ts/, use: { ...devices['Desktop Safari'] } },
    { name: 'mobile-android', testMatch: /browsers\.spec\.ts/, use: { ...devices['Pixel 7'] } },
    { name: 'mobile-ios', testMatch: /browsers\.spec\.ts/, use: { ...devices['iPhone 14'] } },
    // README screenshots (npx playwright test --project=docs); not a test run.
    { name: 'docs', testMatch: /capture-docs\.spec\.ts/, use: { ...devices['Desktop Chrome'] } },
  ],
});
