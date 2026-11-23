"""Check the sheet format identifiers required by drawing review rule 10."""

import re
import unicodedata

from signature_checks import drawing_notes


COMPANY = '深圳市大族数控科技股份有限公司'
OLD_COMPANY = '深圳市大族数控科技有限公司'
TEMPLATE = 'CQR-07-001-2020-07-02-A1'


def _normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).upper()


def inspect_sheet_format(raw, title_block):
    """Keep a result for each required identifier, even when its evidence is missing."""
    keys = ('company', 'template_code', 'process_field')
    if not title_block or re.search(r'^SheetFormatVisible: False\s*$', raw, re.M):
        return [dict(field=key, result='pending', entries=[]) for key in keys]

    left, bottom, right, top = title_block
    middle = (left+right)/2
    lower_band = bottom+(top-bottom)/2
    candidates = {key: [] for key in keys}
    scoped_unknown = False
    for note in drawing_notes(raw):
        x0, y0, x1, y1 = note['bounds']
        x, y = (x0+x1)/2, (y0+y1)/2
        text = _normalized(note['text'])
        in_block = left < x < right and bottom < y < top
        if not text or not left < x < right or not 0 <= y < top:
            continue
        if not note['owner'] or (note['owner'] == '2' and not note['confirmedVisible']):
            scoped_unknown = True
        if note['owner'] != '2' or not note['confirmedVisible']:
            continue
        if in_block and x > middle and y < lower_band and text in (COMPANY, OLD_COMPANY):
            candidates['company'].append(dict(note, matches=text == COMPANY))
        code = re.fullmatch(r'(?:编号:)?(CQR-[A-Z0-9]+(?:-[A-Z0-9]+){2,})', text)
        if code and x > middle and y < lower_band:
            candidates['template_code'].append(dict(note, matches=code[1] == TEMPLATE))
        if in_block and text in ('工艺', '工艺:'):
            candidates['process_field'].append(dict(note, matches=False))

    text_inventory_available = (not scoped_unknown
                                and re.search(r'^SheetFormatVisible: True\s*$', raw, re.M)
                                and re.search(r'^TemplatePictures: 0 Blocks: 0\s*$', raw, re.M)
                                and 'NoteMetadataReadError:' not in raw
                                and not re.search(r'^(?:DrawingTable|TableReadError):', raw, re.M)
                                and bool(candidates['company'] or candidates['template_code']))
    checks = []
    for key, entries in candidates.items():
        if any(not entry['matches'] for entry in entries):
            result = 'failed'
        elif entries or (key == 'process_field' and text_inventory_available):
            result = 'passed'
        else:
            result = 'pending'
        checks.append(dict(field=key, result=result, entries=entries))
    return checks
