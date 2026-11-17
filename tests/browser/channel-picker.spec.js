import {test, expect} from '@playwright/test';

test('channel picker commits pointer selections and supports keyboard return to Latest',async({page})=>{
  await page.goto('/');
  await page.getByRole('button',{name:'Latest',exact:true}).click();
  // A held pointer exposes WebKit's blur-before-click sequence.
  await page.getByRole('option',{name:/^science /}).click({delay:150});
  await expect(page).toHaveURL(/channel=science/);
  await expect(page.locator('.feed-row')).toHaveCount(3);
  await expect(page.locator('.feed-row h2')).toHaveText(['Science reading 2','Science reading 1','Science reading 0']);
  await page.locator('#channel-trigger').click();
  await page.getByRole('option',{name:/^technology /}).click({delay:150});
  await expect(page).toHaveURL(/channel=technology/);
  await expect(page.locator('.feed-row')).toHaveCount(3);
  await expect(page.locator('.feed-row h2')).toHaveText(['Technology reading 2','Technology reading 1','Technology reading 0']);
  await page.locator('#channel-trigger').click();
  await page.keyboard.press('Home');await page.keyboard.press('Enter');
  await expect(page.locator('#channel-trigger')).toHaveText('Latest ⌄');
  await expect(page.locator('.feed-row')).not.toHaveCount(3);
  await page.locator('#channel-trigger').click();await page.keyboard.press('Escape');
  await expect(page.locator('#channel-trigger')).toBeFocused();
  await expect(page.locator('#channel-options')).toBeHidden();
});
