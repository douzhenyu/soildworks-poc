import urllib.request,json,time,pathlib
j=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/jobs/'+pathlib.Path('D:/shukong/review-app/test-job-id.txt').read_text()))
assert j['status']=='awaiting_confirmation'
assert [x['pages'] for x in j['files']]==[1,2]
assert not any(x.get('reviewed') for x in j['files'])
r=urllib.request.Request('http://127.0.0.1:8765/api/jobs/'+j['id']+'/review',data=json.dumps({'rules':list(range(1,13))}).encode(),headers={'Content-Type':'application/json'})
j=json.load(urllib.request.urlopen(r))
for _ in range(30):
 j=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/jobs/'+j['id']))
 if j['status']!='reviewing':break
 time.sleep(1)
assert j['status']=='completed',j
print('REVIEW',[(f['name'],len(f['issues']),f['vision']) for f in j['files']])
assert any('Arail' in a['reason'] for f in j['files'] for a in f['issues'])
assert any('N.M' in a['reason'] for f in j['files'] for a in f['issues'])
assert urllib.request.urlopen('http://127.0.0.1:8765/api/jobs/'+j['id']+'/bundle').read(2)==b'PK'
print('PASS: real ZIP -> SolidWorks -> 3 PDF pages -> explicit confirmation -> rule review -> downloadable marked PDFs')

