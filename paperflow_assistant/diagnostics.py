"""Safe structured diagnostics: counters and allowlisted SDK limits, never raw errors."""
import re


def usage_snapshot(usage):
    values = {key: getattr(usage, key, 0) for key in ('input_tokens', 'output_tokens', 'requests', 'tool_calls')}
    values['total_tokens'] = values['input_tokens']+values['output_tokens']
    return values


def budget_snapshot(settings, phase='curation'):
    return {'requests': 2 if phase == 'evaluation' else settings.request_limit,
            'total_tokens': settings.evaluation_token_limit if phase == 'evaluation' else settings.token_limit,
            'tool_calls': None if phase == 'evaluation' else settings.tool_limit,
            'output_tokens_per_request': settings.max_output_tokens if phase == 'curation' else None,
            'decision_calls': settings.decision_limit, 'decision_depth': settings.decision_depth}


def failure_details(exc, settings, usage, phase='curation'):
    error = type(exc).__name__
    values = usage_snapshot(usage)
    detail = {'error_type': error, 'phase': phase, 'category': 'execution',
              'usage': values, 'budgets': budget_snapshot(settings, phase), 'limit': None,
              'configured_limit': None, 'observed': None, 'measurement': 'unknown'}
    if error == 'UsageLimitExceeded':
        detail['category'] = 'budget'
        suffix = '. Consider raising the limit, or see the docs on usage limits for budget-aware patterns: https://pydantic.dev/docs/ai/core-concepts/agent/#usage-limits'
        message = str(exc).removesuffix(suffix)
        # These are the pinned SDK's numeric-only messages. Unrecognized text is discarded.
        match = re.fullmatch(r'The next request would exceed the request_limit of (\d+)', message)
        if match:
            detail.update(limit='requests', configured_limit=int(match[1]),
                          observed=values['requests']+1, measurement='projected_next_request')
        else:
            match = re.fullmatch(r'(?:Exceeded the|The next request would exceed the) (input_tokens|output_tokens|total_tokens)_limit of (\d+) \(\1=(\d+)\)', message)
            if match:
                detail.update(limit=match[1], configured_limit=int(match[2]), observed=int(match[3]), measurement='reported_usage')
            else:
                match = re.fullmatch(r'The next tool call\(s\) would exceed the tool_calls_limit of (\d+) \(tool_calls=(\d+)\)\.?', message)
                if match:
                    detail.update(limit='tool_calls', configured_limit=int(match[1]), observed=int(match[2]), measurement='projected_tool_calls')
    elif error == 'OutputTokenLimitExceeded':
        detail.update(category='budget', limit='output_tokens_per_request', configured_limit=budget_snapshot(settings, phase)['output_tokens_per_request'])
    elif isinstance(exc, TimeoutError) or error == 'RunTimedOut':
        detail.update(category='timeout', limit='duration_seconds', configured_limit=60 if phase == 'evaluation' else settings.timeout_seconds)
    return detail
