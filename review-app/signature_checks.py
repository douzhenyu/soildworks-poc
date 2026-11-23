"""Locate approval fields from template geometry and inspect their note text."""

import re

from slot_checks import numbers


ROLES = ('设计', '校对', '标准化', '审核', '批准')
EPSILON = 1e-6


def template_lines(raw):
    horizontal, vertical = [], []
    for a, b in re.findall(r'^FrameLine: (.+) -> (.+)$', raw, re.M):
        start, end = numbers(a, 3), numbers(b, 3)
        if not start or not end:
            continue
        x, y, _ = start
        u, v, _ = end
        if abs(y-v) <= EPSILON and abs(x-u) > EPSILON:
            horizontal.append((round(y, 6), min(x, u), max(x, u)))
        if abs(x-u) <= EPSILON and abs(y-v) > EPSILON:
            vertical.append((round(x, 6), min(y, v), max(y, v)))
    return horizontal, vertical


def drawing_notes(raw):
    pattern = (r'^Note: (.*?)\nNoteExtent: ([^\n]+)'
               r'(?:\n(?:(?!Note:|View: |DIM: |DisplayDimensions: |NoteMetadata)[^\n]*\n)*?'
               r'NoteMetadata: visibility=(\d+) \| layer=(\w+)'
               r'(?: \| owner=(\d+))? \| name=([^\n]*))?')
    seen = set()
    format_hidden = bool(re.search(r'^SheetFormatVisible: False\s*$', raw, re.M))
    for text, extent, visibility, layer, owner, name in re.findall(pattern, raw, re.M | re.S):
        coordinates, _, link = extent.partition(' Linked:')
        points = numbers(coordinates, 6)
        if not points or visibility in ('2', '3') or layer == 'hidden' or (owner == '2' and format_hidden):
            continue
        x, y, _, u, v, _ = points
        bounds = (min(x,u), min(y,v), max(x,u), max(y,v))
        text = re.sub(r'</?FONT(?:\s[^>]+)?>', '', text, flags=re.I).strip(' \t\r\n\u200b\ufeff\xa0')
        key = (text, tuple(round(value, 6) for value in bounds), link.strip(), visibility, layer, owner)
        if key in seen:
            continue
        seen.add(key)
        yield dict(text=text, bounds=bounds, link=link.strip(), name=name.strip(), owner=owner,
                   confirmedVisible=visibility == '1' and layer == 'visible')


def box_center(bounds):
    x, y, u, v = bounds
    return (x+u)/2, (y+v)/2


def box_contains(box, point):
    x, y, u, v = box
    return x+EPSILON < point[0] < u-EPSILON and y+EPSILON < point[1] < v-EPSILON


def _approval_cells(label, title_block, horizontal, vertical):
    x, y = box_center(label)
    left, bottom, right, top = title_block
    xs = sorted({position for position, lo, hi in vertical
                 if left-EPSILON <= position <= right+EPSILON and lo < y < hi})
    before = [position for position in xs if position < x]
    after = [position for position in xs if position > x]
    if not before or len(after) < 3:
        return None
    columns = [before[-1], *after[:3]]
    ys = sorted({position for position, lo, hi in horizontal
                 if bottom-EPSILON <= position <= top+EPSILON
                 and lo <= columns[0]+EPSILON and hi >= columns[3]-EPSILON})
    below = [position for position in ys if position < y]
    above = [position for position in ys if position > y]
    if not below or not above:
        return None
    row_bottom, row_top = below[-1], above[0]
    for position in columns:
        if not any(px == position and lo <= row_bottom+EPSILON and hi >= row_top-EPSILON
                   for px, lo, hi in vertical):
            return None
    # Approval layouts have a compact role label followed by two similarly sized fields.
    widths = [u-v for v, u in zip(columns, columns[1:])]
    if (not .002 <= row_top-row_bottom <= .012 or min(widths) < .005
            or max(widths) > .035 or abs(widths[1]-widths[2]) > .005
            or not box_contains((columns[0], row_bottom, columns[1], row_top), (x, y))):
        return None
    return ((columns[1], row_bottom, columns[2], row_top),
            (columns[2], row_bottom, columns[3], row_top))


def inspect_signature_cells(raw, title_block):
    if title_block is None:
        return []
    horizontal, vertical = template_lines(raw)
    notes = list(drawing_notes(raw))
    result = []
    for role in ROLES:
        candidates = {cells for note in notes
                      if note['text'] == role and box_contains(title_block, box_center(note['bounds']))
                      if (cells := _approval_cells(note['bounds'], title_block, horizontal, vertical))}
        if len(candidates) != 1:
            continue
        for field, box in zip(('签名', '日期'), candidates.pop()):
            entries = [note for note in notes if box_contains(box, box_center(note['bounds']))]
            filled = [note for note in entries if note['text'] and note['confirmedVisible']
                      and not note['text'].startswith('$PRP')]
            uncertain = [note for note in entries if note['text'] and note not in filled]
            state = 'filled' if filled else 'unresolved' if uncertain else 'no_visible_note_text'
            result.append(dict(role=role, field=field, result=state, cellBounds=box,
                               entries=filled or uncertain,
                               linkedProperties=sorted({note['link'] for note in entries if '$PRP' in note['link']})))
    return result
