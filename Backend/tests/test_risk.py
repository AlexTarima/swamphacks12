from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import database
from main import app
import ml_risk
import risk
from risk_sources import RiskSources
from weather import HOURLY_FIELDS, weather_response

NOW = datetime(2026, 10, 17, 10, 15, tzinfo=timezone(timedelta(hours=-4)))
SITE = {"id": 1, "name": "Test site", "latitude": 25.75, "longitude": -80.7}


def sources():
    end = NOW.replace(minute=0)
    times = [(end + timedelta(hours=hour)).replace(tzinfo=None).isoformat(timespec='minutes')
             for hour in range(-72, 49)]
    hourly = {field: [0.0] * len(times) for field in HOURLY_FIELDS}
    hourly['time'] = times
    hourly['apparent_temperature'] = [75.0] * len(times)
    hourly['soil_moisture_3_to_9cm'] = [0.2] * len(times)
    raw = {
        'current': {field: hourly[field][72] for field in HOURLY_FIELDS},
        'hourly': hourly, 'timezone': 'America/New_York', 'utc_offset_seconds': -14400,
        'current_units': {}, 'hourly_units': {'rain': 'inch', 'showers': 'inch'},
    }
    raw['current']['time'] = NOW.replace(tzinfo=None).isoformat(timespec='minutes')
    data = {
        'weather': weather_response(raw, SITE['latitude'], SITE['longitude']),
        'alerts': {'alerts': []}, 'wildfires': {'events': [], 'unsupported_geometries': 0},
        'wildlife': {'total_results': 0, 'sightings': []},
    }
    return {name: {'status': 'available', 'data': item, 'retrieved_at': NOW.isoformat()}
            for name, item in data.items()}


class TemporalRiskTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('risk.utcnow', return_value=NOW))

    def test_tomorrows_heat_is_only_upcoming(self):
        feeds = sources()
        feeds['weather']['data']['hourly']['apparent_temperature'][23] = 115
        result = risk.assess(SITE, feeds)
        self.assertEqual(result['current_risk']['indicators']['extreme_heat']['level'], 'low')
        self.assertEqual(result['hourly_predicted_risk'][23]['indicators']['extreme_heat']['level'], 'severe')
        self.assertEqual(result['highest_upcoming_risk']['overall']['level'], 'severe')
        self.assertEqual(result['highest_upcoming_risk']['time'], '2026-10-18T10:00:00-04:00')
        self.assertEqual(result['overall'], result['current_risk']['overall'])
        self.assertEqual(result['indicators']['extreme_heat']['level'], 'low')

    def test_current_heat_does_not_contaminate_hourly_forecast(self):
        feeds = sources()
        feeds['weather']['data']['current']['apparent_temperature'] = 115
        result = risk.assess(SITE, feeds)
        self.assertEqual(result['current_risk']['indicators']['extreme_heat']['level'], 'severe')
        self.assertTrue(all(frame['indicators']['extreme_heat']['level'] == 'low'
                            for frame in result['hourly_predicted_risk']))

    def test_current_alert_uses_wall_clock_and_preserves_observation_time(self):
        feeds = sources()
        feeds['weather']['data']['current']['time'] = '2026-10-17T12:00'
        feeds['alerts']['data']['alerts'] = [{
            'event': 'Flash Flood Warning', 'severity': 'Severe',
            'onset': '2026-10-17T12:05:00-04:00', 'expires': '2026-10-17T13:00:00-04:00',
        }]
        actual_now = NOW.replace(hour=12, minute=10)
        with patch('risk.utcnow', return_value=actual_now):
            current = risk.assess(SITE, feeds)['current_risk']
        self.assertEqual(current['indicators']['flooding']['level'], 'high')
        self.assertEqual(current['weather_observed_at'], '2026-10-17T12:00:00-04:00')
        self.assertEqual(current['alerts_evaluated_at'], actual_now.isoformat())
        self.assertEqual(current['time'], actual_now.isoformat())
        self.assertEqual(feeds['weather']['data']['current']['time'], '2026-10-17T12:00')
        feeds['alerts']['data']['alerts'][0]['expires'] = '2026-10-17T12:08:00-04:00'
        with patch('risk.utcnow', return_value=actual_now):
            self.assertEqual(risk.assess(SITE, feeds)['current_risk']['indicators']['flooding']['evidence']['alerts'], [])

    def test_forecast_and_strike_availability_are_separate(self):
        for code in (0, 95):
            feeds = sources()
            feeds['weather']['data']['current']['weather_code'] = code
            feeds['weather']['data']['hourly']['weather_code'] = [code] * 48
            result = risk.assess(SITE, feeds)
            for frame in [result['current_risk'], *result['hourly_predicted_risk']]:
                lightning = frame['indicators']['lightning']
                self.assertEqual(lightning['thunderstorm_forecast']['availability'], 'available')
                self.assertEqual(lightning['measured_lightning_strikes']['availability'], 'unknown')
                self.assertEqual(lightning['measured_lightning_strikes']['level'], 'unknown')
                self.assertIsNone(lightning['measured_lightning_strikes']['count'])
                self.assertNotEqual(lightning['coverage'], 'available')
                self.assertFalse(frame['overall']['coverage_complete'])

    def test_future_alerts_start_and_end_at_correct_hour(self):
        feeds = sources()
        feeds['alerts']['data']['alerts'] = [{
            'event': 'Extreme Heat Warning', 'severity': 'Extreme',
            'onset': '2026-10-18T10:00:00-04:00', 'ends': '2026-10-18T12:00:00-04:00',
            'expires': '2026-10-18T13:00:00-04:00',
        }]
        result = risk.assess(SITE, feeds)
        self.assertEqual(result['current_risk']['indicators']['extreme_heat']['level'], 'low')
        hourly = result['hourly_predicted_risk']
        self.assertEqual(hourly[22]['indicators']['extreme_heat']['level'], 'low')
        self.assertEqual(hourly[23]['indicators']['extreme_heat']['level'], 'severe')
        self.assertEqual(hourly[25]['indicators']['extreme_heat']['level'], 'low')

    def test_future_rain_does_not_enter_current_or_earlier_hours(self):
        feeds = sources()
        feeds['weather']['data']['hourly']['precipitation'][23] = 4
        result = risk.assess(SITE, feeds)
        self.assertEqual(result['current_risk']['indicators']['flooding']['level'], 'low')
        self.assertEqual(result['hourly_predicted_risk'][22]['indicators']['flooding']['level'], 'low')
        self.assertEqual(result['hourly_predicted_risk'][23]['indicators']['flooding']['level'], 'severe')
        self.assertEqual(result['hourly_predicted_risk'][47]['indicators']['flooding']['level'], 'low')

    def test_missing_hour_remains_unknown_instead_of_shifting_next_hour(self):
        feeds = sources()
        for values in feeds['weather']['data']['hourly'].values():
            del values[5]
        result = risk.assess(SITE, feeds)
        self.assertEqual(len(result['hourly_predicted_risk']), 48)
        self.assertEqual(result['hourly_predicted_risk'][5]['indicators']['extreme_heat']['level'], 'unknown')
        self.assertFalse(result['highest_upcoming_risk']['forecast_data_complete'])

    def test_all_feeds_missing_remain_unknown(self):
        feeds = sources()
        for feed in feeds.values():
            feed.update(status='unavailable', data=None)
        result = risk.assess(SITE, feeds)
        self.assertEqual(result['current_risk']['overall']['level'], 'unknown')
        self.assertIsNone(result['highest_upcoming_risk']['time'])


class ModelContractTests(unittest.TestCase):
    def setUp(self):
        self.request = ml_risk.request_for(risk.assess(SITE, sources()))

    def output(self):
        return {
            'request_id': self.request['request_id'], 'model_version': 'teammate-model-v1',
            'predictions': [{'time': self.request['samples'][24]['time'], 'score': 8.7,
                             'construction_hazards': ['Heat stress', 'Equipment traction loss']}],
        }

    def test_model_score_is_returned_unchanged_with_time_hazards_and_version(self):
        result = ml_risk.parse_prediction(self.output(), self.request)
        self.assertIsNone(result['current_score'])
        self.assertEqual(result['hourly_scores'][23]['score'], 8.7)
        self.assertEqual(result['highest_upcoming']['score'], 8.7)
        self.assertEqual(result['model_version'], 'teammate-model-v1')
        self.assertEqual(result['highest_upcoming']['construction_hazards'],
                         ['Heat stress', 'Equipment traction loss'])
        self.assertEqual(result['availability'], 'partial')

    def test_bad_score_and_mismatched_time_are_rejected_not_clamped(self):
        for score in (-1, 11, float('nan'), float('inf'), True):
            output = self.output()
            output['predictions'][0]['score'] = score
            with self.assertRaises(ValueError):
                ml_risk.parse_prediction(output, self.request)
        for time in ('2020-01-01T00:00:00+00:00', '2026-10-18T10:00:00'):
            output = self.output()
            output['predictions'][0]['time'] = time
            with self.assertRaises(ValueError):
                ml_risk.parse_prediction(output, self.request)
        output = self.output()
        output['request_id'] = 'old input'
        with self.assertRaises(ValueError):
            ml_risk.parse_prediction(output, self.request)

    def test_features_have_no_rule_levels_or_synthetic_ml_labels(self):
        for sample in self.request['samples']:
            self.assertNotIn('level', sample['features'])
            self.assertNotIn('score', sample['features'])
            self.assertIsNone(sample['features']['measured_lightning_strike_count'])

    def test_individual_predictions_merge_only_with_same_model_and_input(self):
        future = ml_risk.parse_prediction(self.output(), self.request)
        output = self.output()
        output['predictions'][0].update(time=self.request['samples'][0]['time'], score=2.1)
        current = ml_risk.parse_prediction(output, self.request)
        merged = ml_risk.merge_predictions(future, current)
        self.assertEqual(merged['current_score'], 2.1)
        self.assertEqual(merged['highest_upcoming']['score'], 8.7)
        self.assertEqual(merged['scored_samples'], 2)
        current['model_version'] = 'new model'
        self.assertIsNone(ml_risk.merge_predictions(future, current)['highest_upcoming'])


class EndpointTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.object(database, 'DATABASE_PATH', Path(directory.name) / 'sites.db'))
        self.enterContext(patch.dict(os.environ, {'PLANIT_ML_URL': ''}))
        self.client = self.enterContext(TestClient(app))
        self.fetch = self.enterContext(patch.object(RiskSources, 'for_site', new_callable=AsyncMock,
                                                    return_value=sources()))

    def test_bulk_refresh_includes_custom_sites_even_after_first_60(self):
        custom = self.client.post('/api/sites', json={'name': 'Crew site', 'latitude': 29, 'longitude': -81}).json()
        response = self.client.post('/api/risks/refresh')
        self.assertEqual(response.status_code, 200, response.text)
        ids = {item['site']['id'] for item in response.json()['risks']}
        self.assertIn(custom['id'], ids)
        self.assertEqual(len(ids), len(database.list_sites()))
        self.assertEqual(len(ids), 62)
        self.assertTrue(all(item['ml_assessment']['current_score'] is None for item in response.json()['risks']))
        page = self.client.post('/api/risks/refresh?limit=1&offset=61').json()
        self.assertEqual(page['risks'][0]['site']['id'], custom['id'])

    def test_teammate_export_and_import_preserves_rule_based_assessment(self):
        request = self.client.get('/api/sites/1/ml-input').json()
        before = database.load_risk(1)['current_risk']
        output = {
            'request_id': request['request_id'], 'model_version': 'test-trained-model',
            'predictions': [{'time': request['samples'][24]['time'], 'score': 9.25,
                             'construction_hazards': ['Heat stress']}],
        }
        response = self.client.post('/api/sites/1/ml-output', json=output)
        self.assertEqual(response.status_code, 200, response.text)
        stored = database.load_risk(1)
        self.assertEqual(stored['current_risk'], before)
        self.assertEqual(stored['ml_assessment']['highest_upcoming']['score'], 9.25)
        self.assertIsNone(stored['ml_assessment']['current_score'])
        returned = self.client.get('/api/sites/1/risk').json()
        self.assertEqual(returned['ml_assessment']['model_version'], 'test-trained-model')
        output['request_id'] = 'stale export'
        self.assertEqual(self.client.post('/api/sites/1/ml-output', json=output).status_code, 409)

    def test_model_output_requires_valid_site_and_score(self):
        output = {'request_id': 'x', 'model_version': 'v1', 'predictions': [
            {'time': NOW.isoformat(), 'score': 11, 'construction_hazards': []}]}
        self.assertEqual(self.client.post('/api/sites/1/ml-output', json=output).status_code, 422)
        self.assertEqual(self.client.get('/api/sites/999999/risk').status_code, 404)

    def test_manual_model_results_survive_refreshes_until_replaced(self):
        request = self.client.get('/api/sites/1/ml-input').json()
        output = {
            'request_id': request['request_id'], 'model_version': 'manual-v1',
            'predictions': [
                {'time': request['samples'][0]['time'], 'score': 0.0, 'construction_hazards': []},
                {'time': request['samples'][24]['time'], 'score': 8.7, 'construction_hazards': ['Heat stress']},
            ],
        }
        self.assertEqual(self.client.post('/api/sites/1/ml-output', json=output).status_code, 200)
        original = database.load_risk(1)['ml_assessment']
        for _ in range(2):
            snapshot = database.load_risk(1)
            snapshot['generated_at'] = (risk.utcnow() - timedelta(minutes=2)).isoformat()
            database.save_risk(1, snapshot)
            response = self.client.get('/api/sites/1/risk')
            self.assertEqual(response.status_code, 200, response.text)
            retained = response.json()['ml_assessment']
            self.assertTrue(retained['stale'])
            self.assertEqual(retained['current'], original['current'])
            self.assertEqual(retained['hourly_scores'], original['hourly_scores'])
            self.assertEqual(retained['model_version'], 'manual-v1')
            self.assertEqual(retained['input_generated_at'], original['input_generated_at'])
        overview = self.client.get('/api/risks').json()['sites'][0]
        self.assertTrue(overview['ml_assessment']['stale'])
        new_request = self.client.get('/api/sites/1/ml-input').json()
        replacement = {'request_id': new_request['request_id'], 'model_version': 'manual-v2',
                       'predictions': [{'time': new_request['samples'][0]['time'], 'score': 2.0,
                                        'construction_hazards': ['Slippery access road']}]}
        response = self.client.post('/api/sites/1/ml-output', json=replacement)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['stale'])
        self.assertEqual(response.json()['current_score'], 2.0)
        self.assertEqual(database.load_risk(1)['ml_assessment']['model_version'], 'manual-v2')

    def test_failed_automatic_inference_also_preserves_predictions(self):
        request = self.client.get('/api/sites/1/ml-input').json()
        output = {'request_id': request['request_id'], 'model_version': 'manual-v1',
                  'predictions': [{'time': request['samples'][0]['time'], 'score': 6.0,
                                   'construction_hazards': ['Wet ground']}]}
        self.client.post('/api/sites/1/ml-output', json=output)
        snapshot = database.load_risk(1)
        snapshot['generated_at'] = (risk.utcnow() - timedelta(minutes=2)).isoformat()
        database.save_risk(1, snapshot)
        with patch('ml_risk.predict', new_callable=AsyncMock,
                   return_value=ml_risk.unavailable('model_request_failed_or_invalid_output')):
            response = self.client.get('/api/sites/1/risk')
        model = response.json()['ml_assessment']
        self.assertEqual(model['current_score'], 6.0)
        self.assertTrue(model['stale'])
        self.assertEqual(model['latest_attempt']['reason'], 'model_request_failed_or_invalid_output')

    def test_old_mixed_snapshot_is_not_exposed_as_current(self):
        site = database.find_site(1)
        old = {'site': site, 'method_version': 'planning-screen-v1', 'generated_at': NOW.isoformat(),
               'overall': {'level': 'severe', 'coverage_complete': True}}
        database.save_risk(1, old)
        overview = self.client.get('/api/risks').json()['sites'][0]
        self.assertIsNone(overview['overall'])
        self.assertEqual(overview['status'], 'pending')
        self.assertEqual(self.client.get('/api/sites/1/risk').json()['method_version'], 'planning-screen-v2')


if __name__ == '__main__':
    unittest.main()
