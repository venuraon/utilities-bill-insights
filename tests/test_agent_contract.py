"""Exercise orchestration without credentials; real schema checks need dependencies."""
import asyncio
import copy
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from utility_demo.agents import (EmailLengthError, explain, fingerprint, run_live,
                                sample_outputs, save_json, validate_email, validate_result,
                                ANALYST_PROMPT, PREVIOUS_ANALYST_PROMPT)

ROOT = Path(__file__).resolve().parents[1]


class AgentBoundaryTests(unittest.TestCase):
    def setUp(self):
        reports = json.loads((ROOT / 'output/data/evidence.json').read_text())
        self.evidence = next(r for r in reports if r['home']['id'] == 'C')

    def mock_sdk(self, first, second, *additional):
        class Output:
            def __init__(self, value):
                self.value = value
            def model_dump(self, mode):
                assert mode == 'json'
                return self.value
        class Agent:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
        sdk = types.ModuleType('agents')
        sdk.Agent = Agent
        sdk.RunConfig = lambda **kwargs: kwargs
        sdk.Runner = types.SimpleNamespace(run=AsyncMock(side_effect=[
            types.SimpleNamespace(final_output=Output(value)) for value in (first, second, *additional)]))
        schemas = types.ModuleType('utility_demo.models')
        schemas.BillingAnalysis = type('BillingAnalysis', (), {})
        schemas.EmailCopy = type('EmailCopy', (), {})
        return sdk, schemas

    def test_only_summary_then_only_analyst_json_crosses_boundaries(self):
        analysis, email = sample_outputs(self.evidence)
        sdk, schemas = self.mock_sdk(analysis, email)
        with patch.dict(sys.modules, {'agents': sdk, 'utility_demo.models': schemas}):
            result = asyncio.run(run_live(self.evidence, 'gpt-6-luna'))
        self.assertEqual(result, (analysis, email))
        first, second = sdk.Runner.run.call_args_list
        self.assertEqual(json.loads(first.args[1]), self.evidence['semantic_summary'])
        self.assertEqual(json.loads(second.args[1]), analysis)
        self.assertEqual(first.args[0].output_type, schemas.BillingAnalysis)
        self.assertEqual(second.args[0].output_type, schemas.EmailCopy)
        self.assertEqual(first.args[0].model, 'gpt-6-luna')
        self.assertTrue(first.kwargs['run_config']['tracing_disabled'])

    def test_bad_analyst_output_stops_before_second_call(self):
        analysis, email = sample_outputs(self.evidence)
        analysis['is_anomaly'] = False
        sdk, schemas = self.mock_sdk(analysis, email)
        with patch.dict(sys.modules, {'agents': sdk, 'utility_demo.models': schemas}):
            with self.assertRaisesRegex(ValueError, 'anomaly flags'):
                asyncio.run(run_live(self.evidence, 'gpt-6-luna'))
        self.assertEqual(sdk.Runner.run.call_count, 1)

    def test_review_required_is_checkpointed_without_communications_call(self):
        analysis, email = sample_outputs(self.evidence)
        analysis['needs_review'] = True
        sdk, schemas = self.mock_sdk(analysis, email)
        with tempfile.TemporaryDirectory() as folder:
            checkpoint = Path(folder) / 'review.analyst.json'
            with patch.dict(sys.modules, {'agents': sdk, 'utility_demo.models': schemas}):
                result = asyncio.run(run_live(self.evidence, 'gpt-6-luna', analyst_checkpoint=checkpoint))
            self.assertEqual(result, (analysis, None))
            self.assertEqual(sdk.Runner.run.call_count, 1)
            self.assertTrue(json.loads(checkpoint.read_text())['needs_review'])
        validate_result(analysis, None, self.evidence)
        with self.assertRaisesRegex(ValueError, 'approved customer email'):
            validate_result(analysis, email, self.evidence)

    def test_normal_home_uses_short_calculated_snapshot_without_communications(self):
        reports = json.loads((ROOT / 'output/data/evidence.json').read_text())
        normal = next(r for r in reports if r['home']['id'] == 'A')
        analysis, unused_email = sample_outputs(normal)
        sdk, schemas = self.mock_sdk(analysis, unused_email)
        with patch.dict(sys.modules, {'agents': sdk, 'utility_demo.models': schemas}):
            result_analysis, email = asyncio.run(run_live(normal, 'gpt-6-luna'))
        self.assertEqual(sdk.Runner.run.call_count, 1)
        self.assertFalse(result_analysis['is_anomaly'])
        self.assertIn('{{daily_change}}', email['explanation'])
        self.assertNotIn('equipment', email['explanation'])
        validate_email(email, normal, result_analysis)

    def test_review_cache_can_resume_without_clearing_flag_or_calling_model(self):
        analysis, email = sample_outputs(self.evidence)
        analysis['needs_review'] = True
        sdk, schemas = self.mock_sdk(analysis, email)
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key-not-real'}):
                with patch.dict(sys.modules, {'agents': sdk, 'utility_demo.models': schemas}):
                    result = asyncio.run(explain(self.evidence, 'gpt-6-luna', 'live', folder))
            self.assertEqual(result['status'], 'review_required')
            self.assertIsNone(result['email'])
            self.assertEqual(sdk.Runner.run.call_count, 1)
            with patch('utility_demo.agents.run_live', new_callable=AsyncMock) as live:
                resumed = asyncio.run(explain(self.evidence, 'gpt-6-luna', 'live', folder, resume=True))
                live.assert_not_called()
            self.assertTrue(resumed['analysis']['needs_review'])
            self.assertEqual(resumed['status'], 'review_required')

    def test_review_flag_does_not_bypass_mathematical_validation(self):
        analysis, email = sample_outputs(self.evidence)
        analysis['needs_review'] = True
        analysis['mathematical_justification'][0]['calculated_value'] = 'wrong'
        sdk, schemas = self.mock_sdk(analysis, email)
        with patch.dict(sys.modules, {'agents': sdk, 'utility_demo.models': schemas}):
            with self.assertRaisesRegex(ValueError, 'calculated value'):
                asyncio.run(run_live(self.evidence, 'gpt-6-luna'))
        self.assertEqual(sdk.Runner.run.call_count, 1)

    def test_weak_weather_does_not_prevent_qualified_anomaly_emails(self):
        reports = json.loads((ROOT / 'output/data/evidence.json').read_text())
        for home in ('C', 'D'):
            evidence = next(r for r in reports if r['home']['id'] == home)
            self.assertLess(evidence['semantic_summary']['weather']['historical_fit_r_squared'], .1)
            self.assertTrue(evidence['semantic_summary']['weather']['temperature_outside_training_range'])
            analysis, email = sample_outputs(evidence)
            sdk, schemas = self.mock_sdk(analysis, email)
            with patch.dict(sys.modules, {'agents': sdk, 'utility_demo.models': schemas}):
                result = asyncio.run(run_live(evidence, 'gpt-6-luna'))
            self.assertFalse(result[0]['needs_review'])
            self.assertIsNotNone(result[1])
            self.assertEqual(sdk.Runner.run.call_count, 2)
            self.assertEqual(sdk.Runner.run.call_args_list[0].args[0].instructions, ANALYST_PROMPT)
        self.assertIn('none alone requires staff review', ANALYST_PROMPT)
        self.assertNotEqual(ANALYST_PROMPT, PREVIOUS_ANALYST_PROMPT)

    def test_resume_revalidates_previous_approved_cache_without_model_calls(self):
        analysis, email = sample_outputs(self.evidence)
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / f"C-{fingerprint(self.evidence, 'gpt-6-luna', analyst_prompt=PREVIOUS_ANALYST_PROMPT)}.json"
            save_json(old, {'analysis': analysis, 'email': email, 'model': 'gpt-6-luna'})
            with patch('utility_demo.agents.run_live', new_callable=AsyncMock) as live:
                result = asyncio.run(explain(self.evidence, 'gpt-6-luna', 'live', folder, resume=True))
                live.assert_not_called()
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(result['provenance']['migrated_from'], old.name)

    def test_previous_review_hold_gets_new_analysis_instead_of_flag_override(self):
        analysis, email = sample_outputs(self.evidence)
        held = {**analysis, 'needs_review': True}
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / f"C-{fingerprint(self.evidence, 'gpt-6-luna', analyst_prompt=PREVIOUS_ANALYST_PROMPT)}.json"
            save_json(old, {'analysis': held, 'email': None, 'model': 'gpt-6-luna'})
            with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key-not-real'}):
                with patch('utility_demo.agents.run_live', new_callable=AsyncMock, return_value=(analysis, email)) as live:
                    result = asyncio.run(explain(self.evidence, 'gpt-6-luna', 'live', folder, resume=True))
                    live.assert_awaited_once()
            self.assertEqual(result['status'], 'ready')
            self.assertFalse(result['analysis']['needs_review'])
            self.assertTrue(json.loads(old.read_text())['analysis']['needs_review'])

    def test_overlong_email_is_rewritten_without_repeating_analyst(self):
        analysis, email = sample_outputs(self.evidence)
        long = {**email, 'intro': 'A longer headline that exceeds the seven word limit'}
        sdk, schemas = self.mock_sdk(analysis, long, email)
        with patch.dict(sys.modules, {'agents': sdk, 'utility_demo.models': schemas}):
            result = asyncio.run(run_live(self.evidence, 'gpt-6-luna'))
        self.assertEqual(result, (analysis, email))
        self.assertEqual(sdk.Runner.run.call_count, 3)
        calls = sdk.Runner.run.call_args_list
        self.assertEqual(sum(c.args[0].output_type == schemas.BillingAnalysis for c in calls), 1)
        feedback = json.loads(calls[-1].args[1])
        self.assertEqual(feedback['analysis'], analysis)
        self.assertEqual(feedback['previous_draft'], long)
        self.assertEqual(feedback['word_limit_violations']['intro'], {'words': 9, 'limit': 7})

    def test_retries_are_bounded_and_checkpoint_can_resume(self):
        analysis, email = sample_outputs(self.evidence)
        long = {**email, 'explanation': 'word ' * 25}
        sdk, schemas = self.mock_sdk(analysis, long, long, long)
        with tempfile.TemporaryDirectory() as folder:
            checkpoint = Path(folder) / 'analyst.json'
            with patch.dict(sys.modules, {'agents': sdk, 'utility_demo.models': schemas}):
                with self.assertRaisesRegex(ValueError, 'Home C: email still too long after 2 revisions'):
                    asyncio.run(run_live(self.evidence, 'gpt-6-luna', analyst_checkpoint=checkpoint))
            self.assertEqual(sdk.Runner.run.call_count, 4)
            self.assertEqual(json.loads(checkpoint.read_text()), analysis)
            # The next invocation only needs the communications call.
            sdk, schemas = self.mock_sdk(email, email)
            with patch.dict(sys.modules, {'agents': sdk, 'utility_demo.models': schemas}):
                result = asyncio.run(run_live(self.evidence, 'gpt-6-luna', analyst_checkpoint=checkpoint, resume=True))
            self.assertEqual(result, (analysis, email))
            self.assertEqual(sdk.Runner.run.call_count, 1)
            self.assertEqual(sdk.Runner.run.call_args.args[0].output_type, schemas.EmailCopy)

    def test_invalid_factual_claim_is_not_treated_as_a_length_issue(self):
        analysis, email = sample_outputs(self.evidence)
        wrong = {**email, 'explanation': 'Your equipment used 9999 kWh.'}
        sdk, schemas = self.mock_sdk(analysis, wrong, email)
        with patch.dict(sys.modules, {'agents': sdk, 'utility_demo.models': schemas}):
            with self.assertRaisesRegex(ValueError, 'Unvalidated number'):
                asyncio.run(run_live(self.evidence, 'gpt-6-luna'))
        self.assertEqual(sdk.Runner.run.call_count, 2)

    def test_counts_use_expanded_metric_values(self):
        analysis, email = sample_outputs(self.evidence)
        email['subject'] = 'Your bill for {{signal_start}} is explained right here'
        with self.assertRaises(EmailLengthError) as caught:
            validate_email(email, self.evidence, analysis)
        self.assertEqual(caught.exception.counts['subject'], 9)
        self.assertIn('subject: 9/8 words', str(caught.exception))

    def test_resume_reuses_complete_cache_but_fresh_live_still_calls(self):
        analysis, email = sample_outputs(self.evidence)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"C-{fingerprint(self.evidence, 'gpt-6-luna')}.json"
            save_json(path, {'analysis': analysis, 'email': email, 'model': 'gpt-6-luna'})
            with patch('utility_demo.agents.run_live', new_callable=AsyncMock) as live:
                result = asyncio.run(explain(self.evidence, 'gpt-6-luna', 'live', folder, resume=True))
                live.assert_not_called()
            self.assertEqual(result['provenance']['mode'], 'cached')
            with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key-not-real'}):
                with patch('utility_demo.agents.run_live', new_callable=AsyncMock, return_value=(analysis, email)) as live:
                    result = asyncio.run(explain(self.evidence, 'gpt-6-luna', 'live', folder))
                    self.assertEqual(live.call_count, 1)
            self.assertEqual(result['provenance']['mode'], 'live')


@unittest.skipUnless(importlib.util.find_spec('pydantic'), 'Install requirements.txt for real Pydantic schema checks')
class PydanticSchemaTests(unittest.TestCase):
    def test_models_validate_and_forbid_extra_fields(self):
        from pydantic import ValidationError
        from utility_demo.models import BillingAnalysis, EmailCopy
        report = json.loads((ROOT / 'output/data/evidence.json').read_text())[2]
        analysis, email = sample_outputs(report)
        self.assertEqual(BillingAnalysis.model_validate(analysis).anomaly_type, 'constant_draw')
        EmailCopy.model_validate(email)
        with self.assertRaises(ValidationError):
            BillingAnalysis.model_validate({**analysis, 'extra': 'forbidden'})
        schema = BillingAnalysis.model_json_schema()
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['required']), set(schema['properties']))

    @unittest.skipUnless(importlib.util.find_spec('agents'), 'Install openai-agents for SDK strict-schema compatibility')
    def test_sdk_accepts_both_strict_schemas(self):
        from agents.agent_output import AgentOutputSchema
        from utility_demo.models import BillingAnalysis, EmailCopy
        for model in (BillingAnalysis, EmailCopy):
            schema = AgentOutputSchema(model)
            self.assertTrue(schema.is_strict_json_schema())
