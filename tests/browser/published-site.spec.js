import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import AxeBuilder from '@axe-core/playwright';

test('published links expand and open the complete curation Markdown', async ({ page }, testInfo) => {
  const directory = testInfo.outputPath('published');
  execFileSync('uv', ['run', '--locked', '--group', 'dev', '--extra', 'test', 'python', '-c',
    "import runpy,sys; from pathlib import Path; runpy.run_path('tests/test_publish_site.py')['_curated_site'](Path(sys.argv[1]))", directory]);
  await page.goto(pathToFileURL(`${directory}/site/index.html`).href);
  const card = page.locator('.curation-card');
  await expect(card.locator('.curation-body')).toBeHidden();
  await card.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(card.locator('.curation-body')).toBeVisible();
  await expect(card).toContainText('A model summary');
  await expect(card.getByRole('link', { name: 'Discussion' })).toHaveAttribute('href', 'https://example.com/discussion');
  await card.getByRole('link', { name: 'Read curation Markdown' }).click();
  const document = page.getByRole('region', { name: 'Curation Markdown' });
  await expect(document).toBeVisible();
  await expect(document.locator('pre')).toContainText('Replication needed.');
  const downloadEvent = page.waitForEvent('download');
  await document.getByRole('link', { name: 'Download Markdown' }).click();
  expect((await downloadEvent).suggestedFilename()).toMatch(/^curation-.*\.md$/);
  await document.getByRole('link', { name: 'Back to link' }).click();
  await expect(document).toBeHidden();
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('curation-card-mobile.png'), fullPage: true });
});
