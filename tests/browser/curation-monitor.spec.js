import {test, expect} from '@playwright/test';

test('curation monitor distinguishes volume from sampled estimated quality', async({page})=>{
  await page.route('**/api/monitor/curation', route=>route.fulfill({json:{enabled:true,
    counts:{succeeded:12,queued:3,running:1,failed:2},eligible_jobs:18,retries:4,
    diagnostics:{failed_attempts:2,completed_attempts:14,consecutive_failures_in_window:1,rolling_tokens_used:18000,rolling_token_limit:100000,rolling_unknown_usage_requests:1,queue_blocked_reason:'retry_backoff',failure_groups:{'UsageLimitExceeded:total_tokens':2},recent_failures:[{error_type:'UsageLimitExceeded',failure:{phase:'curation',limit:'total_tokens',observed:130525,configured_limit:120000,measurement:'reported_usage'}}]},
    active_memory:{follow_up:5},valid_quote_citations:26,
    spend:{window_days:7,estimated_usd:'0.123456',estimated_usd_per_completed_job:'0.010288',retry_estimated_usd:'0.01',
      input_tokens:12000,output_tokens:3000,priced_requests:15,unpriced_requests:1,pending_requests:0,by_role_and_model:{}},
    quality:{sample_evaluated:3,sample_selected:4,evaluation_errors:1,
      groups:{'evidence-v1:judge':{n:3,passed:2,pass_rate:2/3,pass_rate_interval_95:[0.21,0.94]}}}}}));
  await page.goto('/sources');
  const panel=page.getByRole('region',{name:'Research curation'});
  await expect(panel).toContainText('12 / 18');
  await expect(panel).toContainText('3 evaluated / 4 selected');
  await expect(panel).toContainText('Evaluation errors: 1');
  await expect(panel).toContainText('Human-calibrated accuracy is unknown');
  await expect(panel).toContainText('provenance, not factual correctness');
  await expect(panel).toContainText('$0.123456');
  await expect(panel).toContainText('12000 / 3000');
  await expect(panel).toContainText('Unknown usage is not free');
  await expect(panel).toContainText('130525 / 120000');
  await expect(panel).toContainText('Rolling 24-hour tokens: 18000 / 100000');
  await expect(panel).toContainText('retry_backoff');
});

test('curation monitor does not report perfect quality for an empty sample', async({page})=>{
  await page.route('**/api/monitor/curation', route=>route.fulfill({json:{enabled:true,counts:{},quality:{groups:{}}}}));
  await page.goto('/sources');
  await expect(page.getByRole('region',{name:'Research curation'})).toContainText('Quality estimate unavailable');
});

test('operations surfaces a curation incident and requests a bounded cycle', async({page})=>{
  await page.route('**/health/ready', route=>route.fulfill({status:503,json:{
    healthy:false, enabled:true, curation:{enabled:true,healthy:false,error:'CapacityExceededError'}}}));
  await page.route('**/api/monitor/curation', route=>route.fulfill({json:{enabled:true}}));
  await page.route('**/api/monitor/publication', route=>route.fulfill({json:{
    status:'succeeded',service_status:'curation_delayed',built_at:'2026-11-20T18:00:00Z'}}));
  await page.route('**/api/monitor/curation/runs', route=>{
    expect(route.request().method()).toBe('POST');
    return route.fulfill({status:202,json:{status:'requested'}});
  });
  await page.goto('/sources');
  await expect(page.getByRole('heading',{name:'Operations'})).toBeVisible();
  await expect(page.locator('#monitor-overview')).toContainText('Curation needs attention');
  await expect(page.locator('#monitor-overview')).toContainText('Curation delayed');
  await expect(page.locator('#sources-alert')).toContainText('CapacityExceededError');
  await page.getByRole('button',{name:'Request curation cycle'}).click();
  await expect(page.locator('#notice')).toContainText('Curation cycle requested');
});
