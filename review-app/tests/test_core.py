import io
import json
import pathlib
import stat
import tempfile
import unittest
import zipfile
import sys
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server
import pymupdf as fitz


class CoreTests(unittest.TestCase):
    def archive(self, names):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            for name in names:z.writestr(name,b'sample')
        b.seek(0)
        return b

    def test_safe_archive(self):
        with tempfile.TemporaryDirectory() as t:
            server.safe_extract(self.archive(['nested/part.SLDDRW']),pathlib.Path(t))
            self.assertTrue((pathlib.Path(t)/'nested/part.SLDDRW').exists())

    def test_unsafe_archives(self):
        for names in [['../escape.SLDDRW'],['/absolute'],['C:/escape'],['a','A']]:
            with self.subTest(names=names),tempfile.TemporaryDirectory() as t:
                with self.assertRaises(ValueError):server.safe_extract(self.archive(names),pathlib.Path(t))

    def test_symlink(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            entry=zipfile.ZipInfo('link');entry.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(entry,'target')
        b.seek(0)
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):server.safe_extract(b,pathlib.Path(t))

    def test_rules_from_real_extraction(self):
        # Real extraction evidence created by the SolidWorks end-to-end test.
        ident_path=server.ROOT/'test-job-id.txt'
        if not ident_path.exists():self.skipTest('Run test-e2e.py to generate real extraction evidence')
        base=server.DATA/ident_path.read_text().strip()
        j=json.loads((base/'job.json').read_text(encoding='utf-8'))
        findings=[]
        for item in j['files']:
            current,_=server.engine(base,item,[4]);findings+=current
        self.assertTrue(any('Arail' in a['reason'] for a in findings))
        self.assertTrue(any('N.M' in a['reason'] for a in findings))
        for item in j['files']:
            current,_=server.engine(base,item,[12])
            self.assertTrue(all(a['rule']==12 and a['severity']=='pending' for a in current))

    def test_missing_cli_is_explicit(self):
        with patch.object(server,'codex_binary',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'Codex CLI'):server.vision([], [5], settings={'provider':'codex-cli'})

    def test_batch_rules_find_rendered_spacing_and_title_collisions(self):
        base = server.DATA / '0d0ca1e2120746fb91cfd5a01fe408c7'
        if not (base / 'job.json').exists():
            self.skipTest('Local 10-drawing regression batch is unavailable')
        job = json.loads((base / 'job.json').read_text(encoding='utf-8'))
        by_name = {item['name']: item for item in job['files']}
        findings, _ = server.engine(base, by_name['负向零件19.SLDDRW'], [5])
        self.assertTrue(any('3250' in item['reason'] for item in findings))
        for part in (37, 49):
            findings, _ = server.engine(base, by_name[f'负向零件{part}.SLDDRW'], [1, 8])
            self.assertTrue(any(item['title'] == '技术要求与明细栏重叠' for item in findings))
            self.assertFalse(any(item['rule'] == 8 for item in findings))

    def test_vision_output_constraints(self):
        response={'issues':[
            {'page':1,'rule':5,'title':'定位需复核','reason':'测试接口返回','box':[.1,.2,.4,.5]},
            {'page':1,'rule':99,'box':[.1,.2,.4,.5]},
            {'page':1,'rule':5,'box':[-1,0,1,1]},
            {'page':1,'rule':5,'box':[.8,.2,.1,.4]},
            {'page':3,'rule':5,'box':[.1,.2,.4,.5]}
        ]}
        def execute(args,**kwargs):
            Path=pathlib.Path
            Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(response),encoding='utf-8')
            return server.subprocess.CompletedProcess(args,0,b'{}',b'')
        d=fitz.open();d.new_page()
        with patch.object(server,'codex_binary',return_value='codex.exe'),patch.object(server.subprocess,'run',side_effect=execute) as mock:
            issues,status=server.vision(d,[5],settings={'provider':'codex-cli'})
        self.assertEqual(len(issues),1);self.assertEqual(issues[0]['severity'],'warning')
        self.assertIn('--image',mock.call_args.args[0]);self.assertIn('--output-schema',mock.call_args.args[0])
        self.assertIn(b'1',mock.call_args.kwargs['input'])
        d.close()

    def test_cad_copies_are_unique_and_ignore_locks(self):
        with tempfile.TemporaryDirectory() as t:
            base=pathlib.Path(t);source=base/'source';source.mkdir()
            for name in ['a.SLDDRW','a.SLDPRT','~$a.SLDPRT']:(source/name).write_bytes(b'cad')
            result=server.subprocess.CompletedProcess([],0,b'',b'')
            with patch.object(server.subprocess,'run',return_value=result):
                mapped=server.prepare_cad(base,source,'unique-task-123456')
            self.assertEqual(len(mapped),2)
            self.assertTrue(all('__unique-task-' in p.name for p in mapped.values()))
            self.assertEqual((source/'a.SLDDRW').read_bytes(),b'cad')

    def test_review_export_contains_reason(self):
        with tempfile.TemporaryDirectory() as t:
            root=pathlib.Path(t);base=root/'test';base.mkdir()
            d=fitz.open();p=d.new_page();p.draw_rect(fitz.Rect(30,30,100,100));d.save(base/'0.pdf');d.close()
            raw='SheetSize_m: 0.297,0.21\nNote: 锁紧力矩7N.M\nNoteExtent: 0.1,0.1,0,0.2,0.12,0, Linked: plain'
            (base/'0.json').write_text(json.dumps([dict(name='图纸1',raw=raw)]),encoding='utf-8')
            j=dict(id='test',files=[dict(id='0',name='test.SLDDRW',status='ready',pages=1)])
            with patch.object(server,'DATA',root),patch.object(server,'vision',return_value=([],'mock CLI')):
                server.review(j,[4])
            self.assertEqual(j['status'],'completed')
            with fitz.open(base/'0-review.pdf') as out:
                self.assertEqual(len(out),1)
                self.assertTrue(any('N.M' in a.info['content'] for a in out[0].annots()))
            self.assertTrue((base/'review.zip').exists())
            self.assertEqual({item['status'] for item in j['files'][0]['issues']}, {'problem'})
            self.assertEqual({item['status'] for item in j['files'][0]['manualNotes']}, {'manual'})

    def test_signature_rule_passes_only_when_all_cells_and_graphics_are_checked(self):
        cells = [dict(role=role,field=field,result='no_visible_note_text',entries=[],
                      cellBounds=(.13+n*.01,.01,.138+n*.01,.015))
                 for n,(role,field) in enumerate((role,field) for role in server.ROLES for field in ('签名','日期'))]
        page = {'raw':'SheetSize_m: 0.297,0.21\nNoteMetadata: visibility=1'}
        with patch.object(server,'drawing_pages',return_value=[page]), \
             patch.object(server,'inspect_signature_cells',return_value=cells), \
             patch.object(server,'graphic_inventory_read',return_value=True) as inventory, \
             patch.object(server,'page_graphics',return_value=[]) as graphics:
            findings, coverage = server.engine(None,{},[9])
            self.assertEqual(coverage[0]['status'],'checked')
            self.assertFalse(findings)
            graphics.return_value = [dict(bounds=None)]
            self.assertEqual(server.engine(None,{},[9])[1][0]['status'],'partial')
            graphics.return_value = []
            inventory.return_value = False
            self.assertEqual(server.engine(None,{},[9])[1][0]['status'],'partial')


if __name__=='__main__':unittest.main()
