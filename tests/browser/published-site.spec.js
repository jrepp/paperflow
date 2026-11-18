import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import {createServer} from 'node:http';
import {readFileSync} from 'node:fs';
import AxeBuilder from '@axe-core/playwright';

test('published links expand and open the complete curation Markdown', async ({ page }, testInfo) => {
  const directory = testInfo.outputPath('published');
  execFileSync('uv', ['run', '--locked', '--group', 'dev', '--extra', 'test', 'python', '-c',
    "import runpy,sys; from pathlib import Path; runpy.run_path('tests/test_publish_site.py')['_curated_site'](Path(sys.argv[1]))", directory]);
  await page.goto(pathToFileURL(`${directory}/site/index.html`).href);
  const card = page.locator('.curation-card');
  const article = page.locator('article[id^="item-"]').first();
  await expect(card.locator(':scope > summary')).toContainText('1 source');
  await expect(card.locator(':scope > summary')).not.toContainText('A paper');
  expect(await article.locator('h2').evaluate((el) => el.getBoundingClientRect().top))
    .toBeLessThan(await article.locator('.item-meta').evaluate((el) => el.getBoundingClientRect().top));
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
    expect(await account.locator('.document-prose').evaluate(el => el.getBoundingClientRect().top)).toBeLessThan((await page.evaluate(() => innerHeight)) - 120);
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


test('anonymous rating is retryable, deduplicates concurrent clicks, and supports changes', async ({page}, testInfo) => {
  const directory = testInfo.outputPath('ratings');
  execFileSync('uv', ['run', '--locked', '--group', 'dev', '--extra', 'test', 'python', '-c',
    "import runpy,sys; from pathlib import Path; runpy.run_path('tests/test_publish_site.py')['test_real_story_publication_discloses_pending_contribution'](Path(sys.argv[1]))", directory]);
  const html = readFileSync(`${directory}/site/index.html`);
  const requests = [];
  let fail = true;
  const server = createServer((req, res) => {
    if (req.url === '/live/api/publish/ratings') {
      const chunks = [];
      req.on('data', chunk => chunks.push(chunk));
      req.on('end', () => {
        const payload = JSON.parse(Buffer.concat(chunks).toString());
        requests.push(payload);
        setTimeout(() => {
          res.writeHead(fail ? 503 : 200, {'Content-Type': 'application/json'});
          res.end(JSON.stringify(fail ? {detail: 'unavailable'} : {rating: payload.rating}));
        }, 100);
      });
    } else { res.writeHead(200, {'Content-Type': 'text/html'}); res.end(html); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    await page.setViewportSize({width: 390, height: 844});
    await page.emulateMedia({reducedMotion: 'reduce'});
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await expect(page.getByText('AI curation')).toBeVisible();
    await expect(page.getByText('Ratings are pseudonymous.')).toBeVisible();
    await expect(page.getByText('Ratings use a private browser identifier')).toBeHidden();
    await page.getByText('How ratings work').click();
    await expect(page.getByText('Ratings use a private browser identifier')).toBeVisible();
    const story = page.locator('article[id^="story-"]');
    const summary = story.locator('.curation-card > summary');
    await expect(summary).toHaveAttribute('aria-label', 'Details 2 sources for Finding');
    await expect(summary).toContainText('2 sources');
    expect(await summary.evaluate(el => getComputedStyle(el, '::before').transitionDuration)).toBe('0s');
    await summary.focus();
    await page.keyboard.press('Enter');
    const details = story.locator('.curation-card');
    const helpful = details.getByRole('button', {name: 'Helpful'});
    const receipt = story.locator(':scope > .rating-receipt');
    await expect(receipt).toBeHidden();
    await details.getByRole('button', {name:'Close'}).click();
    await expect(receipt).toBeHidden();
    await summary.click();
    await helpful.click();
    await expect(details.locator('.rating-status')).toContainText('Could not save');
    await expect(receipt).toBeHidden();
    await expect(details).toHaveAttribute('open', '');
    expect(requests).toHaveLength(1);
    expect(Object.keys(requests[0]).sort()).toEqual(['id', 'kind', 'rating', 'version']);
    expect(requests[0].kind).toBe('story');
    expect(requests[0].rating).toBe('helpful');
    fail = false;
    await helpful.evaluate(el => { el.click(); el.click(); });
    await expect(details).not.toHaveAttribute('open', '');
    await expect(summary).toBeFocused();
    await expect(page.locator('#rating-announcement')).toHaveText('Thank you. Your feedback was saved.');
    await expect(receipt).toBeVisible();
    await expect(receipt).toHaveText('Feedback saved: Helpful.');
    expect(requests).toHaveLength(2);
    await page.keyboard.press('Space');
    await expect(helpful).toHaveAttribute('aria-pressed', 'true');
    await details.getByRole('button', {name:'Needs work'}).click();
    await expect(details).not.toHaveAttribute('open', '');
    expect(requests).toHaveLength(3);
    expect(requests[2].rating).toBe('needs_work');
    await expect(receipt).toHaveText('Feedback saved: Needs work.');
    await page.keyboard.press('Enter');
    await expect(details.getByRole('button', {name:'Needs work'})).toHaveAttribute('aria-pressed', 'true');
    await details.getByRole('button', {name:'Close'}).click();
    await expect(summary).toBeFocused();
    expect(requests).toHaveLength(3);
    await story.getByRole('link', {name: 'Finding', exact: true}).click();
    const document = page.getByRole('region', {name: 'Story and curation'});
    await document.getByRole('button', {name: 'Helpful'}).click();
    await expect(document).toBeHidden();
    await expect(story).toBeFocused();
    await expect(page.locator('#rating-announcement')).toHaveText('Thank you. Your feedback was saved.');
    expect(requests).toHaveLength(4);
    expect(requests[3].kind).toBe('story');
    await expect(receipt).toHaveText('Feedback saved: Helpful.');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    const result = await new AxeBuilder({page}).analyze();
    expect(result.violations).toEqual([]);
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('curated item sends its exact run version and uncurated cards have no vote controls', async ({page}, testInfo) => {
  const directory = testInfo.outputPath('item-rating');
  execFileSync('uv', ['run', '--locked', '--group', 'dev', '--extra', 'test', 'python', '-c',
    "import runpy,sys; from pathlib import Path; runpy.run_path('tests/test_publish_site.py')['_curated_site'](Path(sys.argv[1]))", directory]);
  const html = readFileSync(`${directory}/site/index.html`);
  const requests = [];
  const server = createServer((req, res) => {
    if (req.url === '/live/api/publish/ratings') {
      const chunks = [];
      req.on('data', chunk => chunks.push(chunk));
      req.on('end', () => {
        const payload = JSON.parse(Buffer.concat(chunks).toString());
        requests.push(payload);
        res.writeHead(200, {'Content-Type': 'application/json'});
        res.end(JSON.stringify({rating: payload.rating}));
      });
    } else { res.writeHead(200, {'Content-Type': 'text/html'}); res.end(html); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const card = page.locator('.curation-card');
    await card.locator(':scope > summary').click();
    const group = card.locator('.card-rating');
    await expect(card.locator('.curation-body > :first-child')).toHaveClass('card-actions');
    await expect(group.getByRole('button', {name: 'Helpful'})).toBeVisible();
    const version = await group.getAttribute('data-version');
    await group.getByRole('button', {name: 'Helpful'}).click();
    await expect(card).not.toHaveAttribute('open', '');
    expect(requests).toHaveLength(1);
    expect(requests[0]).toEqual({kind: 'item', id: await group.getAttribute('data-id'),
      version, rating: 'helpful'});
    expect(version).toMatch(/^[a-f0-9]{64}$/);
    await card.locator(':scope > summary').click();
    await card.getByRole('link', {name: 'Read curation Markdown'}).click();
    const document = page.getByRole('region', {name: 'Curation Markdown'});
    await expect(document.getByRole('button', {name: 'Helpful'})).toHaveAttribute('aria-pressed', 'true');
    await document.getByRole('button', {name: 'Needs work'}).click();
    await expect(document).toBeHidden();
    await expect(page.locator('article[id^="item-"]').first()).toBeFocused();
    expect(requests).toHaveLength(2);
    expect(requests[1].version).toBe(version);
    await expect(page.locator('article[id^="item-"]').first().locator(':scope > .rating-receipt')).toHaveText('Feedback saved: Needs work.');
  } finally { await new Promise(resolve => server.close(resolve)); }
});
