import asyncio
import copy
import json
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from utility_demo.agents import (explain, fingerprint, resolve, sample_outputs,
                                 validate_analysis, validate_email)
from utility_demo.billing import calculate, is_peak
from utility_demo.data import generate, generate_neighborhood
from utility_demo.evidence import agent_input, build_evidence, compare_neighborhood
from utility_demo.render import email_html, normal_snapshot_text, render, shared_weather_svg, weather_response_note
from utility_demo.temperature import fahrenheit, simulation_celsius
from utility_demo.data_factory import agent_input, anomaly_features, correlation, degree_fit, predict, validate_intervals, weather_features

ROOT = Path(__file__).resolve().parents[1]


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'config.json').read_text())

    def test_peak_boundaries_weekend_and_utc_conversion(self):
        local = ZoneInfo('America/Phoenix')
        for stamp, expected in [('2026-03-02T15:55', False), ('2026-03-02T16:00', True),
                                ('2026-03-02T20:55', True), ('2026-03-02T21:00', False),
                                ('2026-03-01T18:00', False)]:
            self.assertEqual(is_peak(datetime.fromisoformat(stamp).replace(tzinfo=local), self.config), expected)
        # 23:00 UTC is 16:00 in Arizona, including after US daylight-saving starts.
        stamp = datetime(2026, 3, 16, 23, tzinfo=timezone.utc).astimezone(local)
        self.assertTrue(is_peak(stamp, self.config))

    def test_known_flat_and_tou_invoices(self):
        config = copy.deepcopy(self.config)
        config['homes'] = [config['homes'][0], config['homes'][2]]
        readings = [{'home_id': h['id'], 'timestamp_utc': stamp, 'kwh': kwh}
                    for h in config['homes']
                    for stamp, kwh in [('2026-03-02T23:00:00+00:00', 2),
                                       ('2026-03-02T22:55:00+00:00', 3),
                                       ('2026-03-02T07:00:00+00:00', 1)]]
        invoices, _, alternatives = calculate(readings, [{'date': '2026-03-02', 'mean_temp_f': 57.2}], config)
        by_home = {i['home_id']: i for i in invoices}
        self.assertEqual(by_home['A']['total'], 10.08)  # 6 × 0.18 + 9
        self.assertEqual(by_home['C']['total'], 13.06)  # 2 × .31 + 4 × .11 + 12
        self.assertEqual(alternatives[('C', '2026-03', 'flat_a')], 10.08)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / 'config.json').read_text())
        cls.temp = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temp.name)
        cls.readings, cls.weather = generate(cls.config, cls.output / 'data')
        cls.neighborhood = generate_neighborhood(cls.config, cls.weather, cls.output / 'data')
        cls.invoices, cls.daily, cls.alternatives = calculate(cls.readings, cls.weather, cls.config)
        cls.evidence = {h['id']: build_evidence(h, cls.daily[h['id']], cls.invoices, cls.alternatives, cls.daily, cls.config, cls.neighborhood) for h in cls.config['homes']}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_complete_readings_and_unique_timestamps(self):
        self.assertEqual(Counter(r['home_id'] for r in self.readings), dict.fromkeys('ABCDE', 25920))
        self.assertEqual(len({(r['home_id'], r['timestamp_utc']) for r in self.readings}), 129600)
        self.assertTrue(all(r['kwh'] >= 0 for r in self.readings))
        for home in 'ABCDE':
            self.assertEqual(len(self.daily[home]), 90)
            self.assertEqual(self.daily[home][0]['date'], '2026-01-01')
            self.assertEqual(self.daily[home][-1]['date'], '2026-03-31')

    def test_all_homes_share_weather_while_usage_response_differs(self):
        expected = [(r['date'], r['mean_temp_f']) for r in self.weather]
        charts = []
        for home in 'ABCDE':
            self.assertEqual([(r['date'], r['temp_f']) for r in self.daily[home]], expected)
            charts.append(shared_weather_svg(self.daily[home], self.config['locality']))
        self.assertEqual(len(set(charts)), 1)
        self.assertIn('degrees Fahrenheit for all five homes', charts[0])
        self.assertIn('Shared cold spell', charts[0])
        homes = {h['id']: h for h in self.config['homes']}
        self.assertIn('a stronger electric-heating response', weather_response_note(homes['B'], self.config))
        self.assertIn('a lower electric-heating response', weather_response_note(homes['A'], self.config))
        self.assertNotEqual([r['kwh'] for r in self.daily['A']], [r['kwh'] for r in self.daily['B']])

    def test_all_invoice_lines_and_readings_reconcile(self):
        self.assertEqual(len(self.invoices), 15)
        tz = ZoneInfo(self.config['timezone'])
        totals = Counter()
        for reading in self.readings:
            month = datetime.fromisoformat(reading['timestamp_utc']).astimezone(tz).strftime('%Y-%m')
            totals[(reading['home_id'], month)] += reading['kwh']
        for invoice in self.invoices:
            lines = sum(Decimal(str(invoice[k])) for k in ('peak_charge', 'off_peak_charge', 'monthly_fee'))
            self.assertEqual(lines, Decimal(str(invoice['total'])))
            self.assertAlmostEqual(invoice['kwh'], totals[(invoice['home_id'], invoice['month'])], places=2)

    def test_anomalies_weather_and_normal_controls(self):
        self.assertEqual(self.evidence['A']['signals'], [])
        self.assertEqual(self.evidence['E']['signals'], [])
        self.assertEqual(self.evidence['B']['signals'][0]['kind'], 'weather')
        self.assertEqual(self.evidence['C']['signals'][0]['kind'], 'sustained')
        self.assertEqual(self.evidence['C']['signals'][0]['start'], '2026-03-08')
        self.assertEqual(self.evidence['D']['semantic_summary']['anomalies']['primary_type'], 'baseline_step')
        self.assertEqual(self.evidence['D']['signals'][0]['start'], '2026-03-15')
        self.assertEqual(self.evidence['C']['semantic_summary']['anomalies']['primary_type'], 'constant_draw')
        self.assertGreater(self.evidence['D']['plans'][0]['savings'], 0)

    def test_agent_inputs_do_not_include_scenario_truth_or_other_home_records(self):
        payload = agent_input(self.evidence['C'])
        self.assertNotIn('injections', payload)
        self.assertNotIn('daily', payload)
        self.assertNotIn('scenario_truth', json.dumps(payload))
        self.assertEqual(payload['home']['id'], 'C')
        self.assertIn('peer_intensity', payload['metrics'])
        self.assertLess(len(json.dumps(payload)), 8000)
        self.assertNotIn('timestamp_utc', json.dumps(payload))
        self.assertNotIn('night_kw', payload)
        self.assertNotIn('heat_sensitivity_kw', payload['home'])
        self.assertIn('z_score', payload['peer_group'])
        self.assertIn('cooling_degree_days', payload['weather'])

    def test_reference_validation_and_cached_fingerprints(self):
        ev = self.evidence['C']
        analysis, email = sample_outputs(ev)
        validate_analysis(analysis, ev)
        validate_email(email, ev, analysis)
        self.assertIn(ev['metrics']['bill_total'], email_html({**ev, 'analysis': analysis, 'email': email, 'provenance': {'mode': 'sample'}}, self.config, ''))
        bad = copy.deepcopy(analysis)
        bad['explanation'] += ' You will save $999.'
        with self.assertRaises(ValueError):
            validate_analysis(bad, ev)
        bad = copy.deepcopy(analysis)
        bad['explanation'] += ' {{invented_saving}}'
        with self.assertRaises(ValueError):
            validate_analysis(bad, ev)
        bad = copy.deepcopy(analysis)
        bad['needs_review'] = True
        with self.assertRaises(ValueError):
            validate_analysis(bad, ev)
        self.assertNotEqual(fingerprint(ev, 'gpt-6-luna'), fingerprint(ev, 'another-model'))
        with self.assertRaisesRegex(ValueError, 'No matching live results'):
            asyncio.run(explain(ev, 'gpt-6-luna', 'cached', self.output / 'no-cache'))

    def test_exact_energy_total_and_data_quality_rejections(self):
        # Use local billing periods, not the UTC calendar month.
        zone = ZoneInfo(self.config['timezone'])
        exact = sum(Decimal(str(r['kwh'])) for r in self.readings
                    if r['home_id'] == 'C' and datetime.fromisoformat(r['timestamp_utc']).astimezone(zone).month == 3)
        self.assertEqual(Decimal(self.evidence['C']['semantic_summary']['billing']['total_kwh']), exact)
        validate_intervals(self.readings, self.weather, self.config)
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            validate_intervals(self.readings[:-1], self.weather, self.config)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            validate_intervals(self.readings + [self.readings[0]], self.weather, self.config)
        with self.assertRaisesRegex(ValueError, 'off-grid'):
            validate_intervals([{**self.readings[0], 'timestamp_utc': '2026-01-01T07:01:00+00:00'}], self.weather, self.config)
        with self.assertRaisesRegex(ValueError, 'weather'):
            validate_intervals(self.readings, self.weather[:-1], self.config)

    def test_strict_decision_cannot_change_flags_metrics_or_plan(self):
        ev = self.evidence['D']
        analysis, _ = sample_outputs(ev)
        for field, value in [('is_anomaly', False), ('anomaly_type', 'none'), ('peer_comparison_status', 'below')]:
            bad = copy.deepcopy(analysis)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_analysis(bad, ev)
        bad = copy.deepcopy(analysis)
        bad['mathematical_justification'][0]['calculated_value'] = 'invented'
        with self.assertRaisesRegex(ValueError, 'calculated value'):
            validate_analysis(bad, ev)
        self.assertIsNotNone(analysis['recommended_plan'])
        for field, value in [('plan_id', 'unregistered'), ('estimated_bill', '0.01'), ('savings', '9999')]:
            bad = copy.deepcopy(analysis)
            bad['recommended_plan'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'optimal calculated plan'):
                validate_analysis(bad, ev)
        bad = copy.deepcopy(analysis)
        bad['unexpected_field'] = True
        with self.assertRaisesRegex(ValueError, 'strict billing schema'):
            validate_analysis(bad, ev)

    def test_peer_z_score_and_zero_variance(self):
        home = self.config['homes'][0]
        base = {'month': '2026-03', 'locality': self.config['locality'], 'heating': 'electric', 'sqft': 900}
        peers = [{**base, 'kwh': value} for value in [100, 200, 300]]
        result = compare_neighborhood(home, 300, peers, self.config)
        self.assertAlmostEqual(result['z_score'], 1.2247, places=4)
        self.assertEqual(result['status'], 'above')
        result = compare_neighborhood(home, 300, [{**base, 'kwh': 200}] * 3, self.config)
        self.assertIsNone(result['z_score'])
        self.assertTrue(result['available'])

    def test_visual_email_copy_limits_and_chart_values(self):
        for ev in self.evidence.values():
            analysis, email = sample_outputs(ev)
            validate_email(email, ev, analysis)
            caption_copy = resolve(' '.join([email['intro'], email['explanation'], *email['actions']]), ev['metrics'])
            self.assertLessEqual(len(caption_copy.split()), 45)
            self.assertEqual(len(email['actions']), 1)
            rendered = email_html({**ev, 'analysis': analysis, 'email': email, 'provenance': {'mode': 'sample'}}, self.config, '')
            self.assertEqual(rendered.count('<svg'), 3 if analysis['is_anomaly'] else 2)
            self.assertIn(ev['metrics']['bill_total'], rendered)
            self.assertIn(ev['metrics']['previous_bill'], rendered)
            self.assertIn(ev['metrics']['daily_change'], rendered)
            self.assertIn('28 → 31', rendered)
            if analysis['is_anomaly']:
                self.assertIn('March daily usage with weather-adjusted estimate', rendered)
            else:
                self.assertNotIn('TRY THIS', rendered)
                self.assertNotIn('Weather-adjusted estimate', rendered)
                self.assertIn('Your March snapshot.', rendered)
            self.assertIn(f"{ev['neighborhood']['peer_kwh']:,.0f} kWh", rendered)
        bad = copy.deepcopy(email)
        bad['explanation'] = 'word ' * 25
        with self.assertRaisesRegex(ValueError, 'word limits'):
            validate_email(bad, ev, analysis)
        bad = copy.deepcopy(email)
        bad['actions'].append('Another action.')
        with self.assertRaisesRegex(ValueError, 'exactly one action'):
            validate_email(bad, ev, analysis)

    def test_similar_size_neighborhood_comparison(self):
        for report in self.evidence.values():
            home, comparison = report['home'], report['neighborhood']
            expected = [p for p in self.neighborhood if home['sqft'] * .8 <= p['sqft'] <= home['sqft'] * 1.2]
            self.assertTrue(comparison['available'])
            self.assertEqual(comparison['count'], len(expected))
            mean = sum(p['kwh'] for p in expected) / len(expected)
            self.assertAlmostEqual(comparison['peer_kwh'], mean, places=2)
            self.assertEqual(comparison['difference_pct'], round((report['invoice']['kwh'] / mean - 1) * 100, 1))
            self.assertNotIn('neighbor_id', json.dumps(agent_input(report)))
            self.assertIn('homes of similar size', comparison['insight'])
        self.assertGreater(self.evidence['C']['neighborhood']['difference_pct'], 0)

    def test_normal_snapshot_does_not_claim_high_peer_usage_is_consistent(self):
        a = self.evidence['A']
        b = self.evidence['B']
        self.assertIn('in line with similar-sized homes', normal_snapshot_text(a))
        self.assertNotIn('in line with', normal_snapshot_text(b))
        self.assertIn('higher than similar-sized homes', normal_snapshot_text(b))

    def test_neighborhood_filters_and_insufficient_data(self):
        home = self.config['homes'][0]  # Size band: 720–1,080 square feet.
        base = {'month': '2026-03', 'locality': self.config['locality'], 'heating': 'electric'}
        peers = [{**base, 'sqft': size, 'kwh': kwh} for size, kwh in [(720, 100), (900, 200), (1080, 300), (1081, 9999)]]
        peers += [{**base, 'sqft': 900, 'kwh': 9999, 'month': '2026-02'},
                  {**base, 'sqft': 900, 'kwh': 9999, 'locality': 'Elsewhere'},
                  {**base, 'sqft': 900, 'kwh': 9999, 'heating': 'gas'}]
        higher = compare_neighborhood(home, 300, peers, self.config)
        self.assertEqual(higher['count'], 3)
        self.assertEqual(higher['peer_kwh'], 200)
        self.assertEqual(higher['difference_pct'], 50)
        self.assertEqual(higher['direction'], 'higher')
        lower = compare_neighborhood(home, 100, peers, self.config)
        self.assertEqual(lower['difference_pct'], -50)
        self.assertEqual(lower['direction'], 'lower')
        unavailable = compare_neighborhood(home, 100, peers[:2], self.config)
        self.assertFalse(unavailable['available'])
        self.assertNotIn('difference_pct', unavailable)

    def test_local_artifacts_and_sample_labels(self):
        reports = []
        for ev in self.evidence.values():
            result = asyncio.run(explain(ev, 'gpt-6-luna', 'sample', self.output / 'cache'))
            reports.append({**ev, **result})
        render(self.config, reports, self.invoices, self.output, ROOT / 'web')
        self.assertEqual(len(list((self.output / 'invoices').glob('*.html'))), 15)
        self.assertEqual(len(list((self.output / 'emails').glob('*.html'))), 5)
        viewer = (self.output / 'demo.html').read_text()
        self.assertNotIn('__STYLE__', viewer)
        self.assertNotIn('__SCRIPT__', viewer)
        self.assertNotIn('__DATA__', viewer)
        self.assertNotIn('<script src=', viewer)
        self.assertNotIn('<link rel="stylesheet"', viewer)
        for path in (self.output / 'emails').glob('*.html'):
            email = path.read_text()
            self.assertIn('SAMPLE EXPLANATION', email)
            self.assertNotIn('{{', email)
            self.assertIn('<svg', email)
            self.assertIn('How you compare nearby', email)
            self.assertIn('neighborhood homes of similar size', email)
        # Model content must remain text, not executable HTML.
        report = copy.deepcopy(next(r for r in reports if r['analysis']['is_anomaly']))
        report['email']['intro'] = '<script>alert("unsafe")</script>'
        safe = email_html(report, self.config, '')
        self.assertIn('&lt;script&gt;', safe)
        self.assertNotIn('<script>', safe)
        self.assertIn('font-family:Arial,sans-serif', safe)
        self.assertIn('viewBox="0 0 580 94"', safe)
        inconsistent = copy.deepcopy(reports)
        inconsistent[1]['daily'][0]['temp_f'] += 1
        with self.assertRaisesRegex(ValueError, 'identical daily weather'):
            render(self.config, inconsistent, self.invoices, self.output / 'invalid-weather', ROOT / 'web')


class WeatherTests(unittest.TestCase):
    def test_fahrenheit_units_and_conversion(self):
        self.assertEqual(fahrenheit(0), 32)
        self.assertEqual(fahrenheit(18), 64.4)
        self.assertEqual(fahrenheit(100), 212)
        self.assertEqual(simulation_celsius(fahrenheit(15.17)), 15.17)

    def test_heating_and_cooling_fit_and_out_of_range_flag(self):
        history = [{'date': f'2026-02-{i+1:02}', 'temp_f': fahrenheit(5 + i),
                    'kwh': 10 + 2 * max(0, 18 - (5+i)) + 3 * max(0, (5+i) - 18)} for i in range(28)]
        fit = degree_fit(history)
        self.assertAlmostEqual(fit['heating_slope'], 2 / 1.8)
        self.assertAlmostEqual(fit['cooling_slope'], 3 / 1.8)
        self.assertAlmostEqual(fit['r_squared'], 1)
        march = [{'date': f'2026-03-{i+1:02}', 'temp_f': fahrenheit(35 + i / 10),
                  'kwh': 10 + 3 * (35 + i/10 - 18)} for i in range(31)]
        result, _ = weather_features(history, march, {})
        self.assertEqual(result['base_temperature_f'], 64.4)
        self.assertEqual(result['degree_day_unit'], '°F·days')
        self.assertNotIn('base_temperature_c', result)
        self.assertAlmostEqual(result['cooling_degree_days'], sum(max(0, r['temp_f'] - 64.4) for r in march), places=3)
        self.assertEqual(result['association'], 'cooling')
        self.assertTrue(result['temperature_outside_training_range'])
        self.assertAlmostEqual(result['unexplained_pct'], 0)
        increased = [{**r, 'kwh': r['kwh'] + 100} for r in march]
        result, _ = weather_features(history, increased, {})
        self.assertEqual(result['association'], 'decoupled')

    def test_constant_temperature_is_not_a_correlation(self):
        self.assertIsNone(correlation([18, 18, 18], [1, 2, 3]))
        self.assertIsNone(correlation([1, 2, 3], [10, 10, 10]))


if __name__ == '__main__':
    unittest.main()
