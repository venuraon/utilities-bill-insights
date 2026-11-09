import copy
import json
import unittest
from pathlib import Path

from utility_demo.agents import sample_outputs
from utility_demo.config import load_config
from utility_demo.customer_insights import customer_insight
from utility_demo.render import email_html, plan_insight_html

ROOT = Path(__file__).resolve().parents[1]


class CustomerInsightTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        reports = json.loads((ROOT / 'output/data/evidence.json').read_text())
        self.reports = {}
        for report in reports:
            # Approvals in live artifacts may vary; tests supply approved fixture outcomes.
            report['analysis'], report['email'] = sample_outputs(report)
            report['provenance'] = {'mode': 'sample', 'model': None}
            self.reports[report['home']['id']] = report

    def test_all_homes_have_one_short_reason_and_matching_action(self):
        for report in self.reports.values():
            insight = customer_insight(report)
            self.assertTrue(insight['reason'])
            self.assertTrue(insight['action'])
            self.assertLessEqual(len(insight['sentence'].split()), 28)
            self.assertIn(';', insight['sentence'])
        b = customer_insight(self.reports['B'])
        self.assertEqual(b['category'], 'weather')
        self.assertIn('Colder days may', b['sentence'])
        self.assertIn('thermostat schedules', b['sentence'])
        self.assertIn('running continuously', customer_insight(self.reports['C'])['reason'])
        self.assertIn('devices left on overnight', customer_insight(self.reports['C'])['action'])
        self.assertIn('schedule or equipment change may', customer_insight(self.reports['D'])['reason'])
        self.assertIn('devices left running', customer_insight(self.reports['D'])['action'])
        for home in ('A', 'E'):
            self.assertEqual(customer_insight(self.reports[home])['category'], 'steady')
            self.assertIn('More billing days', customer_insight(self.reports[home])['reason'])

    def test_guidance_uses_features_not_hardcoded_household_identity(self):
        renamed = copy.deepcopy(self.reports['B'])
        renamed['home']['id'] = 'Other'
        self.assertEqual(customer_insight(renamed), customer_insight(self.reports['B']))
        cooling = copy.deepcopy(renamed)
        cooling['semantic_summary']['weather']['association'] = 'cooling'
        self.assertIn('cooling use', customer_insight(cooling)['reason'])
        cooling['semantic_summary']['billing']['daily_usage_change_pct'] = -20
        self.assertEqual(customer_insight(cooling)['title'], 'Daily consumption decreased')

    def test_plan_opportunities_are_visible_for_normal_and_anomalous_homes(self):
        for home, report in self.reports.items():
            body = email_html(report, self.config, '')
            self.assertIn('POSSIBLE REASON &amp; NEXT STEP', body)
            self.assertIn(customer_insight(report)['sentence'], body)
            best = report['plans'][0]
            if best['savings'] > .5:
                self.assertIn(f"${best['savings']:.2f} less", body)
                self.assertIn(best['plan_name'], body)
                self.assertIn(f"${best['total']:.2f}", body)
                self.assertIn('Future savings may differ', body)
            else:
                self.assertIn('current plan is already lowest cost', body)
                self.assertNotIn('$0.00 less', body)
        b = email_html(self.reports['B'], self.config, '')
        self.assertIn('$24.60 less', b)
        self.assertIn('Evening Flex', b)
        self.assertNotIn('No unusual pattern detected', b)
        self.assertIn('$16.66 less', email_html(self.reports['D'], self.config, ''))
        self.assertIn('$5.37 less', email_html(self.reports['A'], self.config, ''))

    def test_review_holds_still_withhold_customer_guidance_and_plan_advice(self):
        report = copy.deepcopy(self.reports['C'])
        report['analysis']['needs_review'] = True
        report['email'] = None
        body = email_html(report, self.config, '')
        self.assertIn('Customer email withheld', body)
        self.assertNotIn('POSSIBLE REASON', body)
        self.assertNotIn('Consider ', body)

    def test_small_saving_is_not_described_as_already_lowest_cost(self):
        report = copy.deepcopy(self.reports['E'])
        report['plans'][0]['total'] -= .25
        report['plans'][0]['savings'] = .25
        body = plan_insight_html(report)
        self.assertIn('$0.25 less', body)
        self.assertNotIn('already lowest cost', body)
