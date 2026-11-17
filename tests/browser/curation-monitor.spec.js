import {test, expect} from '@playwright/test';

test('curation monitor distinguishes volume from sampled estimated quality', async({page})=>{
  await page.route('**/api/monitor/curation', route=>route.fulfill({json:{enabled:true,
    counts:{succeeded:12,queued:3,running:1,failed:2},eligible_jobs:18,retries:4,
    active_memory:{follow_up:5},valid_quote_citations:26,
    quality:{sample_evaluated:3,sample_selected:4,evaluation_errors:1,
      groups:{'evidence-v1:judge':{n:3,passed:2,pass_rate:2/3,pass_rate_interval_95:[0.21,0.94]}}}}}));
  await page.goto('/sources');
  const panel=page.getByRole('region',{name:'Research curation'});
  await expect(panel).toContainText('12 / 18');
  await expect(panel).toContainText('3 evaluated / 4 selected');
  await expect(panel).toContainText('Evaluation errors: 1');
  await expect(panel).toContainText('Human-calibrated accuracy is unknown');
  await expect(panel).toContainText('provenance, not factual correctness');
});

test('curation monitor does not report perfect quality for an empty sample', async({page})=>{
  await page.route('**/api/monitor/curation', route=>route.fulfill({json:{enabled:true,counts:{},quality:{groups:{}}}}));
  await page.goto('/sources');
  await expect(page.getByRole('region',{name:'Research curation'})).toContainText('Quality estimate unavailable');
});
