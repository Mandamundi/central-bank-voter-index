"""openai_api.py (identical in the three projects): request shape, output parsing and the batch round trip, with no network."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import openai_api
import prompt

PAYLOAD = {'policy_relevant': True, 'level': 5, 'evidence': ['Hold.'], 'rationale': 'A restrictive hold.'}


def response_body(payload=PAYLOAD, status='completed'):
    return {'status': status, 'output': [
        {'type': 'reasoning', 'summary': []},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(payload)}]}]}


def reply(data=None, text=''):
    return SimpleNamespace(status_code=200, json=lambda: data, text=text)


class RequestTests(unittest.TestCase):
    def test_schema_is_strict(self):
        schema = openai_api.strict_schema(prompt.SpeechScore.model_json_schema())
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['required'], ['policy_relevant', 'evidence', 'rationale', 'level'])
        self.assertNotIn('title', json.dumps(schema))
        level = schema['properties']['level']['anyOf']
        self.assertEqual((level[0]['minimum'], level[0]['maximum'], level[1]['type']), (0, 10, 'null'))

    def test_body(self):
        body = openai_api.request_body('m', 'medium', 100, 'SYSTEM', 'USER', prompt.SpeechScore.model_json_schema())
        self.assertEqual((body['instructions'], body['input'], body['reasoning']), ('SYSTEM', 'USER', {'effort': 'medium'}))
        self.assertTrue(body['text']['format']['strict'])
        self.assertNotIn('temperature', body)

    def test_output_text(self):
        self.assertEqual(json.loads(openai_api.output_text(response_body())), PAYLOAD)
        with self.assertRaises(ValueError):
            openai_api.output_text(response_body(status='incomplete'))
        refusal = {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'no'}]}]}
        with self.assertRaises(ValueError):
            openai_api.output_text(refusal)

    def test_error_code(self):
        error = openai_api.APIError(429, json.dumps({'error': {'code': 'insufficient_quota'}}))
        self.assertEqual(error.code, 'insufficient_quota')
        self.assertEqual(openai_api.APIError(500, 'not json').code, '')


class BatchTests(unittest.TestCase):
    def test_submit_then_collect(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'batches.json'
            queue = openai_api.BatchQueue('key', path, 'm:v')
            body = openai_api.request_body('m', 'medium', 100, 'S', 'U' * 350, {'type': 'object', 'properties': {}})
            posts = [reply({'id': 'file-1'}), reply({'id': 'batch-1', 'status': 'validating'}),
                     reply({'id': 'file-2'}), reply({'id': 'batch-2', 'status': 'validating'})]
            with patch('openai_api.requests.post', side_effect=posts) as post:
                # each request is ~150 tokens: a 400-token budget fits two, split one per batch by max_requests
                sent = queue.submit([('a', 'ta', body), ('b', 'tb', body), ('c', 'tc', body)], 400, 1)
            self.assertEqual(sent, 2)
            uploaded = post.call_args_list[0].kwargs['files']['file'][1].decode()
            self.assertEqual(json.loads(uploaded)['url'], '/v1/responses')
            self.assertEqual(queue.pending(), {'a': 'ta', 'b': 'tb'})

            out = '\n'.join(json.dumps({'custom_id': i, 'error': None,
                                        'response': {'status_code': 200, 'body': response_body()}}) for i in ('a',))
            gets = [reply({'status': 'completed', 'output_file_id': 'out-1', 'error_file_id': None}),
                    reply(text=out),
                    reply({'status': 'in_progress'})]
            seen = []
            with patch('openai_api.requests.get', side_effect=gets):
                counts = openai_api.BatchQueue('key', path, 'm:v').collect(
                    lambda i, tag, text: seen.append((i, tag, json.loads(text))) or True, lambda i, r: None)
            self.assertEqual(seen, [('a', 'ta', PAYLOAD)])
            self.assertEqual(counts, {'scored': 1, 'failed': 0, 'still_running': 1})
            self.assertEqual(openai_api.BatchQueue('key', path, 'm:v').pending(), {'b': 'tb'})
            self.assertEqual(openai_api.BatchQueue('key', path, 'other:v').pending(), {})


if __name__ == '__main__':
    unittest.main()
