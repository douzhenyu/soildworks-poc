import json,pathlib,pymupdf as f,sys
sys.path.insert(0,'D:/shukong/review-app')
import server
base=server.DATA/'474f2af9b2784e47aee9a49254c317a6'
with f.open(base/'1.pdf') as d:
 issues,status=server.vision(d,[4,5,6,7],base/'cli-multipage-test',json.loads((base/'1.json').read_text(encoding='utf-8-sig')))
print(status,flush=True);print(json.dumps(issues,ensure_ascii=True),flush=True)
assert '全部2页' in status
(base/'cli-multipage-test/validated.json').write_text(json.dumps(issues,ensure_ascii=False,indent=2),encoding='utf-8')
