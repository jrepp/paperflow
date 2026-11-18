"""Persist each request's usage and a versioned estimate, including failed calls."""
from datetime import UTC, datetime
from decimal import Decimal
import time
import json
import logging
from uuid import uuid4

from pydantic_ai.models.wrapper import WrapperModel


def estimate_cost(usage, prices, requested_model, at):
    if prices.model != requested_model:
        return None, 'unpriced_model'
    cached = usage.get('cache_read_tokens', 0)
    incoming, outgoing = usage['input_tokens'], usage['output_tokens']
    if min(incoming, outgoing, cached) < 0 or cached > incoming:
        return None, 'invalid_usage'
    utc = at.astimezone(UTC)
    peak = utc.weekday() < 5 and (1 <= utc.hour < 4 or 6 <= utc.hour < 10)
    if utc.date().isoformat() in prices.holiday_dates_utc:
        peak = False
    multiplier = Decimal('1') if peak else Decimal(str(prices.off_peak_multiplier))
    total = ((incoming-cached)*Decimal(str(prices.input_per_million))
             + cached*Decimal(str(prices.cached_input_per_million))
             + outgoing*Decimal(str(prices.output_per_million))) * multiplier / Decimal(1000000)
    return str(total), 'peak' if peak else 'off_peak'


logger = logging.getLogger('paperflow.curation')


class MeteredModel(WrapperModel):
    def __init__(self, wrapped, context, role):
        super().__init__(wrapped)
        self.context = context
        self.role = role

    async def request(self, messages, model_settings, model_request_parameters):
        ctx = self.context
        request_id = str(uuid4())
        started = time.time()
        prices = ctx.settings.reasoning_prices if self.role in ('reasoning', 'story_assessment') else ctx.settings.decision_prices
        requested = ctx.settings.reasoning_model if self.role in ('reasoning', 'story_assessment') else ctx.settings.decision_model
        ctx.store.start_request(request_id, ctx.job['id'], ctx.job['attempts'], self.role, requested, started)
        logger.info(json.dumps({'event': 'curation.request.started', 'run_id': ctx.job['id'],
            'attempt': ctx.job['attempts'], 'request_id': request_id, 'role': self.role, 'model': requested}))
        try:
            response = await super().request(messages, model_settings, model_request_parameters)
        except BaseException as exc:
            ctx.store.finish_request(request_id, {'status': 'failed', 'error_type': type(exc).__name__,
                'duration_seconds': time.time()-started, 'usage': None, 'estimated_usd': None})
            logger.warning(json.dumps({'event': 'curation.request.failed', 'run_id': ctx.job['id'],
                'attempt': ctx.job['attempts'], 'request_id': request_id, 'role': self.role,
                'model': requested, 'error_type': type(exc).__name__, 'duration_seconds': time.time()-started}))
            raise
        usage = {key: getattr(response.usage, key, 0) for key in
                 ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')}
        cost, tier = estimate_cost(usage, prices, requested, datetime.fromtimestamp(started, UTC))
        ctx.store.finish_request(request_id, {'status': 'succeeded', 'returned_model': response.model_name,
            'duration_seconds': time.time()-started, 'usage': usage, 'estimated_usd': cost,
            'price_tier': tier, 'prices': prices.model_dump(),
            'cost_basis': 'Configured token prices at request start; not an invoice.'})
        logger.info(json.dumps({'event': 'curation.request.finished', 'run_id': ctx.job['id'],
            'attempt': ctx.job['attempts'], 'request_id': request_id, 'role': self.role, 'model': requested,
            'duration_seconds': time.time()-started, 'usage': usage, 'estimated_usd': cost}))
        return response
