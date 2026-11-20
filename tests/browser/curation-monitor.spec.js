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
  await page.goto('/sources#curation-overview');
  const panel=page.getByRole('region',{name:'Research curation'});
  await expect(panel).toContainText('12 / 18');
  await expect(panel).toContainText('130525 / 120000');
  await expect(panel).toContainText('Rolling 24-hour tokens: 18000 / 100000');
  await expect(panel).toContainText('retry_backoff');
  await page.getByRole('link',{name:'Quality',exact:true}).click();
  const quality=page.getByRole('region',{name:'Quality',exact:true});
  await expect(quality).toContainText('3 evaluated / 4 selected');
  await expect(quality).toContainText('Evaluation errors: 1');
  await expect(quality).toContainText('Human-calibrated accuracy is unknown');
  await expect(quality).toContainText('provenance, not factual correctness');
  await page.getByRole('link',{name:'Usage and spend',exact:true}).click();
  const spend=page.getByRole('region',{name:'Usage and spend'});
  await expect(spend).toContainText('$0.123456');
  await expect(spend).toContainText('12000 / 3000');
  await expect(spend).toContainText('Unknown usage is not free');

});

test('curation monitor does not report perfect quality for an empty sample', async({page})=>{
  await page.route('**/api/monitor/curation', route=>route.fulfill({json:{enabled:true,counts:{},quality:{groups:{}}}}));
  await page.goto('/sources#quality');
  await expect(page.getByRole('region',{name:'Quality'})).toContainText('Quality estimate unavailable');
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
  await page.getByRole('link',{name:'Diagnostics',exact:true}).click();
  await page.getByRole('button',{name:'Request curation cycle'}).click();
  await expect(page.locator('#notice')).toContainText('Curation cycle requested');
});

test('pipeline shows the affected stage first while retaining successful work',async({page})=>{
  await page.route('**/api/monitor/curation',route=>route.fulfill({json:{enabled:true,stages:[
    {stage:'assessment',condition:'overdue',queued:19,running_since:1760000000,attempts_24h:{succeeded:3,failed:2,interrupted:1},
      last_failure:{error:'CapacityExceededError',at:1760000000},consecutive_failures_24h:2},
    {stage:'analysis',condition:'completing',queued:2,attempts_24h:{succeeded:12,failed:0,interrupted:0},last_success_at:1760000000}]}}));
  await page.goto('/sources');
  const stages=page.getByRole('region',{name:'Curation pipeline'}).getByRole('article');
  await expect(stages.first()).toContainText('Overdue run');
  await expect(stages.first()).toContainText('CapacityExceededError');
  await expect(stages.last()).toContainText('Completing work');
  await expect(stages.last()).toContainText('12 succeeded');
});

test('a bookmarked pane survives reload and Back restores the previous pane',async({page})=>{
  await page.goto('/sources#source-list');
  const nav=page.getByRole('navigation',{name:'Operations sections'});
  await expect(nav.getByRole('link',{name:'Source feeds'})).toHaveAttribute('aria-current','page');
  await nav.getByRole('link',{name:'Run history'}).click();
  await expect(page.getByRole('heading',{name:'Recent ingestion runs'})).toBeVisible();
  await expect(page.locator('#source-list')).toBeHidden();
  await page.reload();
  await expect(page.locator('#recent-runs')).toBeVisible();
  await page.goBack();
  await expect(page.locator('#source-list')).toBeVisible();
  await expect(page.locator('#recent-runs')).toBeHidden();
});
