import pathlib,zipfile,urllib.request,json,time
root=pathlib.Path('D:/shukong'); zip_path=root/'review-app/test-upload.zip'
with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
 for n in [39,41]:
  for ext in ['SLDDRW','SLDPRT']:
   p=root/'负向样本-外发/负向样本-外发'/f'负向零件{n}.{ext}';z.write(p,'图纸包/'+p.name)
r=urllib.request.Request('http://127.0.0.1:8765/api/jobs',data=zip_path.read_bytes(),headers={'Content-Type':'application/zip','X-File-Name':'test-upload.zip'})
j=json.load(urllib.request.urlopen(r));print('JOB',j['id'],flush=True)
(root/'review-app/test-job-id.txt').write_text(j['id'])
for _ in range(360):
 j=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/jobs/'+j['id']))
 if j['status'] not in ['queued','extracting','converting']:break
 time.sleep(1)
print(json.dumps(j,ensure_ascii=True),flush=True)
assert j['status']=='awaiting_confirmation',j
assert [f['pages'] for f in j['files']]==[1,2]
assert not any(f.get('reviewed') for f in j['files'])
r=urllib.request.Request('http://127.0.0.1:8765/api/jobs/'+j['id']+'/review',data=json.dumps({'rules':list(range(1,13))}).encode(),headers={'Content-Type':'application/json'})
j=json.load(urllib.request.urlopen(r))
for _ in range(1800):
 j=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/jobs/'+j['id']))
 if j['status']!='reviewing':break
 time.sleep(1)
assert j['status']=='completed',j
print('REVIEW',[(f['name'],len(f['issues']),f['vision']) for f in j['files']])
assert any('Arail' in a['reason'] for f in j['files'] for a in f['issues'])
assert any('N.M' in a['reason'] for f in j['files'] for a in f['issues'])
assert urllib.request.urlopen('http://127.0.0.1:8765/api/jobs/'+j['id']+'/bundle').read(2)==b'PK'
print('PASS: real ZIP -> SolidWorks -> 3 PDF pages -> explicit confirmation -> rule review -> downloadable marked PDFs')


