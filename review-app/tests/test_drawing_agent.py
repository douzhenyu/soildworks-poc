import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class DrawingAgentTest(unittest.TestCase):
    def test_complete_agent_review_does_not_execute_fallback_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root/'job'
            folder.mkdir()
            with server.fitz.open() as pdf:
                pdf.new_page()
                pdf.save(folder/'0.pdf')
            (folder/'0.json').write_text(json.dumps([{'raw':'','name':'Test'}]),encoding='utf-8')
            item = {'id':'0','name':'test.SLDDRW','status':'ready'}
            job = {'id':'job','files':[item]}
            def agent(*args, audit, **kwargs):
                audit['ruleReviews'] = [{'rule':7,'status':'problem','reason':'Test chain'}]
                return [server.issue(1,7,'Test chain','Test chain',[.1,.2,.3,.4],method='Codex CLI 审图 Agent')], 'done'
            with patch.object(server,'DATA',root), patch.object(server,'vision',side_effect=agent), \
                 patch.object(server,'engine') as fallback:
                server.review(job,[7],{'provider':'codex-cli'})
                fallback.assert_not_called()
            self.assertEqual(job['status'],'completed')
            self.assertEqual(item['reviewSource'],'agent')
            self.assertEqual(item['issues'][0]['status'],'problem')

    def test_dimension_evidence_separates_model_envelope_from_cropped_pdf_view(self):
        raw = ('SheetSize_m: 0.3,0.2\nView: Front Model: C:/a/part.SLDPRT\n'
               'ViewMetadata: type=4 | scale=1 | configuration=Default\n'
               'ViewTransform: 1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0\n'
               'ViewDimensionContext: cropped=True | breakLines=0 | breakOuts=0 | modelBreak=False | baseView=\n'
               'ViewEnvelopeStatus: complete | solidBodies=1\n'
               'ViewEnvelopeExtreme: 0 | -1 | 0.01,0.02,0\n'
               'ViewEnvelopeExtreme: 0 | 1 | 0.09,0.02,0\n'
               'ViewEnvelopeExtreme: 1 | -1 | 0.01,0.02,0\n'
               'ViewEnvelopeExtreme: 1 | 1 | 0.01,0.07,0\n'
               'DIM: D1 | SystemValue: 0.008 | Type: 2 | Reference: True | ToleranceType: 0\n'
               'Parentheses: False\n'
               'DimPosition: 0.04,0.02,0 | Dangling: False | AttachedCount: 1 | AttachedTypes: 3 | ReferencePoints: 0\n'
               'DimensionAttachmentContext: 0 | 3 | bracket-1 | Default\n'
               'AttachmentPersistentReference: 0 | model | ABC')
        with server.fitz.open() as doc:
            doc.new_page(width=600,height=400)
            page=server.build_evidence(doc,[{'raw':raw}],'test',server.template_regions)['pages'][0]
        envelope=next(e for e in page['elements'] if e['kind']=='modelEnvelope')
        self.assertAlmostEqual(envelope['spansM']['x'],.08)
        self.assertIsNone(envelope['pdfBoxPt'])
        dim=next(e['constraintEvidence'] for e in page['elements'] if e['kind']=='dimension')
        self.assertTrue(dim['referenceApi'])
        self.assertFalse(dim['shownAsReference'])
        self.assertEqual(dim['attachments'][0]['persistentReferences'],{'model':'ABC'})
        self.assertEqual(dim['attachments'][0]['component'],'bracket-1')

    def test_pdf_calibration_and_page_mismatch(self):
        raw = ('SheetSize_m: 0.3,0.2\nView: Front Model: part.SLDPRT\n'
               'Note: Test\nNoteExtent: 0.03,0.04,0,0.06,0.08,0 Linked: plain\n'
               'ModelFeature: Cut | Hole | suppressed=False')
        with server.fitz.open() as doc:
            page = doc.new_page(width=600, height=400)
            for a,b in [((21,19),(581,19)),((21,379),(581,379)),
                        ((21,19),(21,379)),((581,19),(581,379))]:
                page.draw_line(a,b)
            regions = lambda *_: ((.01,.01,.29,.19), None)
            packet = server.build_evidence(doc, [{'raw':raw}], 'part.SLDDRW', regions)
            row = packet['pages'][0]
            self.assertEqual(row['calibration'], 'pdf-frame')
            note = next(e for e in row['elements'] if e['kind']=='note')
            self.assertEqual(note['pdfBoxPt'], [61,239,121,319])
            feature = next(e for e in row['elements'] if e['kind']=='modelFeature')
            self.assertIsNone(feature['pdfBoxPt'])
            missing = server.build_evidence(doc, [], 'part.SLDDRW', regions)
            self.assertEqual(missing['pageAlignment'], 'unavailable')
            self.assertEqual(missing['pages'][0]['elements'], [])

    def test_agent_finding_uses_same_page_evidence_and_requires_all_rules(self):
        raw = 'SheetSize_m: 0.3,0.2\nNote: Test\nNoteExtent: 0.03,0.04,0,0.06,0.08,0 Linked: plain'
        response = {'ruleReviews':[dict(rule=4,status='problem',reason='misspelling')],
                    'issues':[dict(page=1,rule=4,status='problem',title='Test',reason='misspelling',
                                   elementIds=['p1-note-1','p2-note-1'],box=[])]}

        def execute(args, **kwargs):
            evidence = json.loads((kwargs['cwd']/'evidence.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence['pages'][0]['elements'][0]['id'], 'p1-note-1')
            self.assertIn(b'evidence.json', kwargs['input'])
            (kwargs['cwd']/'evidence-reads.jsonl').write_text('{"page":1}\n', encoding='utf-8')
            Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(response), encoding='utf-8')
            return server.subprocess.CompletedProcess(args,0,b'',b'')

        with server.fitz.open() as doc, tempfile.TemporaryDirectory() as work:
            doc.new_page(width=600,height=400)
            with patch.object(server,'codex_binary',return_value='codex.exe'), \
                 patch.object(server.subprocess,'run',side_effect=execute):
                audit = {}
                results, _ = server.vision(doc,[4],work,[{'raw':raw}],{'provider':'codex-cli'},audit=audit)
                self.assertEqual(audit['ruleReviews'],response['ruleReviews'])
                self.assertEqual(results[0]['box'],[.1,.6,.2,.8])
                self.assertEqual(results[0]['elementIds'],['p1-note-1'])
                self.assertEqual(results[0]['positionSource'],'api')
                partial = {}
                results, _ = server.vision(doc,[4,5],work,[{'raw':raw}],{'provider':'codex-cli'},audit=partial)
                self.assertEqual(partial['ruleReviews'],response['ruleReviews'])
                self.assertEqual(results[0]['elementIds'],['p1-note-1'])

    def test_custom_provider_queries_only_requested_page_and_kind(self):
        call = dict(id='q1',type='function',function=dict(name='read_drawing_evidence',
                    arguments='{"page":2,"kind":"note"}'))
        answer = {'ruleReviews': [], 'issues': []}
        first_call = dict(id='q0',type='function',function=dict(name='read_drawing_evidence',
                          arguments='{"page":1}'))
        responses = [dict(tool_calls=[first_call,call], reasoning_content='prior reasoning'),
                     dict(content=json.dumps(answer))]
        connection = Mock()
        connection.getresponse.side_effect = [
            Mock(status=200, read=Mock(return_value=json.dumps({'choices':[{'message':message}]}).encode()))
            for message in responses]
        packet = {'pages':[{'page':1,'elements':[]},{'page':2,'elements':[
            {'id':'p2-note-1','kind':'note'},{'id':'p2-dimension-1','kind':'dimension'}]}]}
        settings = {'baseUrl':'http://127.0.0.1:8001/v1','model':'mock'}
        with patch.object(server.http.client,'HTTPConnection',return_value=connection):
            self.assertEqual(server.custom_vision(settings,'prompt',[],packet),answer)
        payload = json.loads(connection.request.call_args.args[2])
        self.assertEqual(payload['messages'][1]['reasoning_content'],'prior reasoning')
        tool_output = json.loads(payload['messages'][-1]['content'])
        self.assertEqual(tool_output['elements'],[{'id':'p2-note-1','kind':'note'}])

    def test_custom_provider_retries_one_transient_timeout(self):
        first, second = Mock(), Mock()
        first.getresponse.side_effect = TimeoutError
        second.getresponse.return_value = Mock(status=200, read=Mock(return_value=b'{}'))
        connect = Mock(side_effect=[first, second])
        endpoint = server.urlparse('https://example.com/v1')
        self.assertEqual(server.request_model_turn(connect,endpoint,'/v1/chat/completions',{}, {}, 2), b'{}')
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_evidence_tool_batches_kinds_without_mixing_pages(self):
        packet = {'pages':[{'page':1,'elements':[{'id':'other','kind':'note'}]},
                           {'page':2,'elements':[{'id':'n','kind':'note'},{'id':'d','kind':'dimension'}]}]}
        result = server.evidence_slice(packet, {'page':2,'kinds':['note','dimension']})
        self.assertEqual(result['groups']['note']['elements'],[{'id':'n','kind':'note'}])
        self.assertEqual(result['groups']['dimension']['elements'],[{'id':'d','kind':'dimension'}])
