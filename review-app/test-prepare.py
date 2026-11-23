import pathlib,sys
sys.path.insert(0,'D:/shukong/review-app')
import server
b=server.DATA/'0d0ca1e2120746fb91cfd5a01fe408c7'
c=server.prepare_cad(b,b/'source',b.name)
print('Prepared',len(c),'unique CAD work files',flush=True)
