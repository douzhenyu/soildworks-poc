import sys,pathlib,json
sys.path.insert(0,'D:/shukong/review-app')
import server,pymupdf as f
base=server.DATA/'474f2af9b2784e47aee9a49254c317a6'
with f.open(base/'0.pdf') as d:
 issues,status=server.vision(d,list(range(1,13)),base/'cli-live-test',json.loads((base/'0.json').read_text(encoding='utf-8-sig')))
print(status,flush=True)
print(json.dumps(issues,ensure_ascii=True),flush=True)
assert 'Codex CLI' in status
(base/'cli-live-test/validated.json').write_text(json.dumps(issues,ensure_ascii=False,indent=2),encoding='utf-8')
