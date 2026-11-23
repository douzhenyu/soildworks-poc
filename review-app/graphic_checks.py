"""Locate sheet graphics that may contain signatures or controlled-file stamps."""

from collections import defaultdict
import re
import unicodedata

from signature_checks import drawing_notes
from slot_checks import numbers


def _box(points):
    if not points:
        return None
    x, y, u, v = points
    if x == u or y == v:
        return None
    return min(x,u), min(y,v), max(x,u), max(y,v)


def page_graphics(raw):
    """Return visible or potentially visible graphics, including unlocated objects."""
    objects = []
    format_hidden = bool(re.search(r'^SheetFormatVisible: False\s*$', raw, re.M))
    for scope, index, value in re.findall(r'^GraphicPicture: (.*?) \| (\d+) \| ([^\n]+)', raw, re.M):
        if scope == 'template' and format_hidden:
            continue
        bounds = _box(numbers(value, 4))
        objects.append(dict(kind='picture', objectId=f'{scope}:picture:{index}', scope=scope, bounds=bounds))
    texts = defaultdict(list)
    for scope, index, text in re.findall(r'^GraphicBlockText: (.*?) \| (\d+) \| ([^\n]*)', raw, re.M):
        if text.strip():
            texts[scope,index].append(text.strip())
    for scope, index, origin, name in re.findall(r'^GraphicBlock: (.*?) \| (\d+) \| ([^|\n]+) \| ([^\n]*)', raw, re.M):
        if scope == 'template' and format_hidden:
            continue
        objects.append(dict(kind='block', objectId=f'{scope}:block:{index}', scope=scope,
                            bounds=None, origin=numbers(origin, 3), name=name.strip(),
                            definitionTexts=texts[scope,index]))
    for index, coords, class_id in re.findall(r'^GraphicOle: (\d+) \| ([^|\n]+) \| ([^\n]*)', raw, re.M):
        objects.append(dict(kind='ole', objectId=f'ole:{index}', scope='sheet',
                            bounds=_box(numbers(coords, 4)), classId=class_id.strip()))
    return objects


def graphic_inventory_read(raw):
    views = len(re.findall(r'^View: ', raw, re.M))
    inventories = len(re.findall(r'^GraphicInventory: ', raw, re.M))
    return (views > 0 and inventories == views+1
            and bool(re.search(r'^GraphicOleCount: \d+\s*$', raw, re.M))
            and 'GraphicReadError:' not in raw)


def overlaps(first, second):
    x, y, u, v = first
    a, b, c, d = second
    return min(u,c) > max(x,a) and min(v,d) > max(y,b)


def watermark_evidence(raw, width, height, objects):
    upper_left = (0, height/2, width/2, height)
    for note in drawing_notes(raw):
        text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', note['text']))
        text = text.strip('「」『』“”【】')
        if text != '大族数控受控文件':
            continue
        x0, y0, x1, y1 = note['bounds']
        x, y = (x0+x1)/2, (y0+y1)/2
        if 0 <= x <= width/2 and height/2 <= y <= height:
            yield dict(kind='text', objectId=note['name'], bounds=note['bounds'],
                       result='controlled_stamp' if note['confirmedVisible'] else 'unverified_text')
    for item in objects:
        if item['bounds'] is None or overlaps(item['bounds'], upper_left):
            yield dict(item, result='unverified_graphic')
