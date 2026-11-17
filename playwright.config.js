import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests/browser',
  fullyParallel: false,
  workers: 1,
  projects: [
    {name:'chromium',use:{browserName:'chromium'}},
    {name:'webkit',testMatch:'channel-picker.spec.js',use:{browserName:'webkit'}},
  ],
  use: {baseURL:'http://127.0.0.1:8765', trace:'retain-on-failure'},
  webServer: {
    command:'uv run --locked --group dev --extra test --extra telemetry python tests/browser/server.py',
    url:'http://127.0.0.1:8765/health/ready',
    reuseExistingServer:false,
    timeout:30000,
  },
});
