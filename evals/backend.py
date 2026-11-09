"""One SDK turn per attempt, standard official endpoint, no automatic retries."""
import time
from .common import dumps
from .normalization import visible_json


class ObservedResponses:
    """Capture the public API receipt before the Agents SDK can reject its status."""
    def __init__(self, resource, receipts, requests):
        self.resource, self.receipts, self.requests = resource, receipts, requests

    async def create(self, *args, **kwargs):
        if self.requests:
            raise RuntimeError('A second request in one budgeted attempt is forbidden')
        self.requests.append(True)
        response = await self.resource.create(*args, **kwargs)
        self.receipts.append(response)
        return response

    def __getattr__(self, name):
        return getattr(self.resource, name)


class ObservedClient:
    def __init__(self, client, receipts, requests):
        self.client, self.receipts, self.requests = client, receipts, requests
        self.responses = ObservedResponses(client.responses, receipts, requests)

    def with_options(self, **kwargs):
        return ObservedClient(self.client.with_options(**kwargs), self.receipts, self.requests)

    def __getattr__(self, name):
        return getattr(self.client, name)


class OpenAIBackend:
    def __init__(self, timeout):
        from openai import AsyncOpenAI
        # Explicit endpoint prevents OPENAI_BASE_URL from routing to another provider.
        self.client = AsyncOpenAI(base_url='https://api.openai.com/v1', max_retries=0,
                                  timeout=timeout)

    async def close(self):
        await self.client.close()

    async def call(self, model_id, prompt, payload, schema, max_tokens, effort=None):
        from agents import Agent, ModelSettings, OpenAIResponsesModel, Runner, RunConfig
        from agents.model_settings import ModelRetrySettings
        from openai.types.shared import Reasoning
        receipts, requests = [], []

        settings = ModelSettings(max_tokens=max_tokens, store=False, preserve_raw_usage=True,
                                 retry=ModelRetrySettings(max_retries=0),
                                 extra_args={'service_tier': 'default'},
                                 reasoning=Reasoning(effort=effort) if effort else None)
        agent = Agent(name='Evaluation task', model=OpenAIResponsesModel(model_id, ObservedClient(self.client, receipts, requests)),
                      instructions=prompt, output_type=schema, model_settings=settings)
        started = time.perf_counter()
        result, error = None, None
        try:
            run = await Runner.run(agent, dumps(payload), max_turns=1,
                                   run_config=RunConfig(tracing_disabled=True))
            result = run.final_output.model_dump(mode='json')
        except Exception as exc:
            # Do not persist exception strings: SDK errors can echo request headers.
            error = {'type': type(exc).__name__, 'status_code': getattr(exc, 'status_code', None),
                     'request_id': getattr(exc, 'request_id', None)}
        response = receipts[0] if receipts else None
        raw = [item.model_dump(mode='json') for item in response.output] if response else []
        candidate = visible_json(raw)
        status = getattr(response, 'status', None)
        incomplete = getattr(getattr(response, 'incomplete_details', None), 'reason', None)
        refusal = any(c.get('type') == 'refusal' for item in raw if item.get('type') == 'message'
                      for c in item.get('content', []))
        if status == 'incomplete':
            execution = 'incomplete'
        elif status == 'failed':
            execution = 'provider_error'
        elif refusal:
            execution = 'refused'
        elif result is not None:
            execution = 'completed'
        elif error and error['status_code']:
            execution = 'api_error'
        elif error and error['type'] == 'APITimeoutError':
            execution = 'timeout'
        else:
            execution = 'schema_error' if response is not None else 'sdk_error'
        if error:
            error['category'] = execution
            error['incomplete_reason'] = incomplete
            # Reproduce local parsing details without retaining arbitrary exception text.
            if candidate is not None and status == 'completed':
                try:
                    schema.model_validate(candidate)
                except Exception as exc:
                    if hasattr(exc, 'errors'):
                        error['validation_details'] = exc.errors(include_url=False, include_input=False, include_context=False)
        usage = response.usage.model_dump(mode='json', exclude_unset=True) if response is not None and response.usage else None
        return {'output': result, 'candidate_raw': candidate, 'raw_output': raw, 'error': error,
                'execution_status': execution, 'response_status': status, 'incomplete_reason': incomplete,
                'actual_model': getattr(response, 'model', None),
                'request_started': bool(requests),
                'provider_error_code': getattr(getattr(response, 'error', None), 'code', None),
                'usage': usage,
                'response_id': response.id if response else None,
                'request_id': getattr(response, '_request_id', None) if response else (error or {}).get('request_id'),
                'latency_seconds': round(time.perf_counter() - started, 4), 'source': 'openai'}
