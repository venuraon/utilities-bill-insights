"""A held household must not cancel unrelated approved households."""
import asyncio
import copy
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import build_demo
from utility_demo.config import load_config
from utility_demo.agents import sample_outputs

ROOT = Path(__file__).resolve().parents[1]


class ReviewBuildTests(unittest.TestCase):
    def test_mixed_build_publishes_approved_emails_and_review_notices(self):
        reports = copy.deepcopy(json.loads((ROOT / 'output/data/evidence.json').read_text()))
        # Real saved artifacts can already contain review holds. Build this test's
        # approved baseline explicitly, then inject exactly one review outcome.
        for result in reports:
            result['analysis'], result['email'] = sample_outputs(result)
            result['status'] = 'ready'
        report = next(r for r in reports if r['home']['id'] == 'C')
        report['analysis']['needs_review'] = True
        report['email'] = None
        report['status'] = 'review_required'
        by_id = {r['home']['id']: r for r in reports}
        invoices = [invoice for r in reports for invoice in (r['invoice'], r['previous_invoice'])]
        for r in reports:
            r['provenance'] = {'mode': 'live', 'model': 'gpt-6-luna', 'label': 'Test fixture'}

        async def fixture_explain(evidence, *args, **kwargs):
            result = by_id[evidence['home']['id']]
            return {key: result[key] for key in ('analysis', 'email', 'provenance')}

        with tempfile.TemporaryDirectory() as folder:
            args = Namespace(output=folder, mode='live', resume=True)
            (Path(folder) / 'data').mkdir()
            with patch.object(build_demo, 'run_analytics', return_value=(reports, invoices)):
                with patch.object(build_demo, 'explain', side_effect=fixture_explain):
                    asyncio.run(build_demo.build(args))
            output = Path(folder)
            queue = json.loads((output / 'review_queue.json').read_text())
            self.assertEqual([r['home_id'] for r in queue], ['C'])
            self.assertTrue(queue[0]['analysis']['needs_review'])
            withheld = (output / 'emails/C-2026-03.html').read_text()
            self.assertIn('Customer email withheld', withheld)
            self.assertNotIn('Your bill, explained.', withheld)
            approved = (output / 'emails/A-2026-03.html').read_text()
            self.assertIn('Your March snapshot.', approved)
            viewer = (output / 'demo_assets/demo.html').read_text()
            self.assertIn('STAFF REVIEW REQUIRED', viewer)
            self.assertTrue((output / 'analyses/C.json').exists())
