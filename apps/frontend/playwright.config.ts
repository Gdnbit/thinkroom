import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  testMatch: /.*\.ui\.spec\.ts/,
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: '../../docs/verification/frontend-ui-acceptance-latest.json' }]],
  use: {
    baseURL: process.env.UI_ACCEPTANCE_BASE_URL || 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chrome',
      use: {
        ...devices['Desktop Chrome'],
        channel: process.env.UI_ACCEPTANCE_CHROME_PATH ? undefined : 'chrome',
        launchOptions: process.env.UI_ACCEPTANCE_CHROME_PATH
          ? { executablePath: process.env.UI_ACCEPTANCE_CHROME_PATH }
          : undefined,
      },
    },
  ],
})
