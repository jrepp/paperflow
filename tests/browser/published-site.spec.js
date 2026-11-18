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
  await expect(page.locator('.model-summary')).toBeVisible();
  await expect(page.locator('.model-summary')).toContainText('A model summary');
  await card.locator(':scope > summary').focus();
  await page.keyboard.press('Enter');
  await expect(card.locator('.curation-body')).toBeVisible();
  await expect(card).toContainText('Findings');
  await expect(card.getByRole('link', { name: 'Discussion' })).toHaveAttribute('href', 'https://example.com/discussion');
  await card.getByRole('link', { name: 'Read curation Markdown' }).click();
  const document = page.getByRole('region', { name: 'Curation Markdown' });
  await expect(document).toBeVisible();
  await expect(document.locator('.document-prose')).toContainText('Replication needed.');
  await expect(document.locator('.document-prose h3').first()).toHaveText('Summary');
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


test('story account is readable, navigable, and safe across viewport sizes', async ({ page }, testInfo) => {
  const directory = testInfo.outputPath('stories');
  execFileSync('uv', ['run', '--locked', '--group', 'dev', '--extra', 'test', 'python', '-c',
    "import runpy,sys; from pathlib import Path; runpy.run_path('tests/test_publish_site.py')['test_real_story_publication_discloses_pending_contribution'](Path(sys.argv[1]))", directory]);
  await page.goto(pathToFileURL(`${directory}/site/index.html`).href);
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({width, height: width === 1440 ? 960 : 844});
    const story = page.locator('article[id^="story-"]');
    await story.getByRole('link', {name: 'Finding', exact: true}).click();
    const account = page.getByRole('region', {name: 'Story and curation'});
    await expect(account).toBeVisible();
    await expect(account.locator('.document-prose')).toContainText('Original account');
    await expect(account.locator('pre')).toHaveCount(0);
    expect(await account.locator('.document-prose').evaluate(el => el.getBoundingClientRect().top)).toBeLessThan(600);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    const result = await new AxeBuilder({page}).analyze();
    expect(result.violations).toEqual([]);
    await page.screenshot({path: testInfo.outputPath(`story-${width}.png`)});
    await account.getByRole('link', {name: 'Back to story'}).click();
    await expect(story).toBeFocused();
    const summary = story.locator('summary');
    await summary.focus();
    await page.keyboard.press('Enter');
    await expect(story.locator('.curation-body')).toBeVisible();
    await page.keyboard.press('Space');
    await expect(story.locator('.curation-body')).toBeHidden();
  }
});
