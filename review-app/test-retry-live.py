import urllib.request,json,time,pathlib
base='http://127.0.0.1:8765/api/jobs/0d0ca1e2120746fb91cfd5a01fe408c7'
j=json.load(urllib.request.urlopen(urllib.request.Request(base+'/retry',method='POST')));print('Retry started',flush=True)
for _ in range(300):
 j=json.load(urllib.request.urlopen(base))
 if j['status'] not in ['queued','extracting','converting']:break
 time.sleep(1)
print(json.dumps({'status':j['status'],'files':[(f['name'],f['status'],f['pages'],f.get('error')) for f in j['files']]},ensure_ascii=True),flush=True)
assert j['status']=='awaiting_confirmation',j.get('error')
assert all(x['status']=='ready' for x in j['files'])
assert sum(x['pages'] for x in j['files'])==11
print('PASS: existing batch repaired, all 10 files / 11 pages ready; confirmation required before review.',flush=True)
