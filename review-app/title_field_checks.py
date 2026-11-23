"""Read required title-block values without treating model metadata as displayed text."""

import re

from graphic_checks import overlaps
from signature_checks import EPSILON, box_center, box_contains, drawing_notes, template_lines


# A1 positions are measured from the right and bottom edges of the verified grid.
# Quantity is reported as supplementary evidence because rule 8 does not name it.
FIELDS = {
    '材料': (('材料',), .058, .0125, True),
    '机型': (('机型',), .025, .0325, True),
    '零件名称': (('名称', '零件名称'), .025, .0265, True),
    '图号': (('代号', '图号'), .025, .0200, True),
    '数量': (('数量',), .058, .0075, False),
}
LINK = re.compile(r'\$(PRPSHEET|PRP):"([^"]+)"', re.I)
PLACEHOLDERS = {'-', '--', '—', '／', '/', 'TBD', 'N/A', '待定', '未填写'}
ESCAPED_PROPERTY_CHARS = {'\\': '\\', 'r': '\r', 'n': '\n', 't': '\t'}


def _property_value(value):
    return re.sub(r'\\([\\rnt])', lambda match: ESCAPED_PROPERTY_CHARS[match[1]], value)


def _enclosing_cell(point, block, horizontal, vertical):
    x, y = point
    left, bottom, right, top = block
    xs = {p for p, lo, hi in vertical if left-EPSILON <= p <= right+EPSILON and lo < y < hi}
    lo = max((p for p in xs if p < x), default=None)
    hi = min((p for p in xs if p > x), default=None)
    if lo is None or hi is None:
        return None
    ys = {p for p, a, b in horizontal
          if bottom-EPSILON <= p <= top+EPSILON and a <= lo+EPSILON and b >= hi-EPSILON}
    low = max((p for p in ys if p < y), default=None)
    high = min((p for p in ys if p > y), default=None)
    if low is None or high is None:
        return None
    if not all(any(x0 == edge and y0 <= low+EPSILON and y1 >= high-EPSILON for x0,y0,y1 in vertical)
               for edge in (lo,hi)):
        return None
    return lo, low, hi, high


def _source_properties(raw):
    result = []
    for row in re.findall(r'^TitleProperty: ([^\r\n]*)', raw, re.M):
        fields = row.split('\t')
        if len(fields) != 7 or fields[2] not in ('0','1','2'):
            continue
        scope, name, status, resolved, linked, original, evaluated = fields
        result.append(dict(scope=scope, name=name, apiStatus=int(status),
                           resolved=resolved == 'True', linked=linked == 'True',
                           raw=_property_value(original), evaluated=_property_value(evaluated)))
    return result


def inspect_title_fields(raw, block, a1, graphics, graphics_read):
    notes = list(drawing_notes(raw))
    horizontal, vertical = template_lines(raw)
    properties = _source_properties(raw)
    source_row = re.search(r'^TitlePropertySource: ([^\r\n]*)', raw, re.M)
    source_parts = [_property_value(part) for part in source_row[1].split('\t')] if source_row else []
    source = (dict(zip(('requestedView','view','model','configuration'), source_parts))
              if len(source_parts) == 4 else None)
    has_tables = bool(re.search(r'^(?:DrawingTable|TableReadError):', raw, re.M))
    for field, (aliases, dx, dy, required) in FIELDS.items():
        check = dict(field=field, required=required, result='pending', cellBounds=None,
                     values=[], sourceView=source, sourceProperties=[p for p in properties if p['name'] in aliases])
        if not block:
            yield check
            continue
        anchors = [note for note in notes
                   if note['owner'] == '2' and box_contains(block, box_center(note['bounds']))
                   and any(name in aliases for _, name in LINK.findall(note['link']))]
        if a1:
            box = _enclosing_cell((block[2]-dx, block[1]+dy), block, horizontal, vertical)
        else:
            cells = {cell for anchor in anchors
                     if (cell := _enclosing_cell(box_center(anchor['bounds']), block, horizontal, vertical))}
            box = cells.pop() if len(cells) == 1 else None
        if box is None:
            yield check
            continue
        check['cellBounds'] = box
        entries = [note for note in notes if note['owner'] != '0' and box_contains(box, box_center(note['bounds']))]
        values = [dict(note, binding=[name for _, name in LINK.findall(note['link'])])
                  for note in entries if note['confirmedVisible']
                  and note['text'] and not note['text'].startswith('$PRP')
                  and note['text'].upper() not in PLACEHOLDERS and note['text'].rstrip('：:') not in aliases]
        if values:
            check['values'] = values
            check['result'] = ('present_linked' if any(set(aliases).intersection(value['binding']) for value in values)
                               else 'present_manual')
        else:
            check['objectIds'] = [note['name'] for note in entries if note['name']]
            unknown = (has_tables or not graphics_read or 'NoteMetadataReadError:' in raw
                       or any(not note['confirmedVisible'] for note in entries)
                       or any(graphic['bounds'] is None or overlaps(box, graphic['bounds']) for graphic in graphics))
            if not unknown:
                check['result'] = ('blank_link' if any(LINK.search(note['link']) for note in entries)
                                   else 'empty')
        yield check
