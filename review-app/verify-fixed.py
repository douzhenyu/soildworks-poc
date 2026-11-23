import pathlib,pymupdf as f
from PIL import Image,ImageDraw
b=pathlib.Path('D:/shukong/review-app/data/0d0ca1e2120746fb91cfd5a01fe408c7');tiles=[]
for n in [6,7]:
 with f.open(b/f'{n}.pdf') as d:
  for i,p in enumerate(d):
   pix=p.get_pixmap(matrix=f.Matrix(1,1));im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples);im.thumbnail((680,480));t=Image.new('RGB',(700,510),'#eee');t.paste(im,((700-im.width)//2,25));ImageDraw.Draw(t).text((10,5),f'{39 if n==6 else 41} / {i+1}',fill='black');tiles.append(t)
out=Image.new('RGB',(1400,1020),'white')
for i,t in enumerate(tiles):out.paste(t,((i%2)*700,(i//2)*510))
out.save('D:/shukong/review-app/fixed-conversion.png')
