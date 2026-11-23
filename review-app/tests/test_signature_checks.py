import unittest

from signature_checks import inspect_signature_cells


def note(text, left, right, *, visibility=1, layer='visible', link=''):
    metadata = '' if visibility is None else f'\nNoteMetadata: visibility={visibility} | layer={layer} | name=Test'
    return (f'Note: {text}\nNoteExtent: {left},0.011,0,{right},0.014,0, Linked: {link}'
            f'{metadata}\n')


GRID = ''.join([
    *(f'FrameLine: {x},0.01,0 -> {x},0.015,0\n' for x in (.13, .14, .155, .17)),
    *(f'FrameLine: 0.13,{y},0 -> 0.17,{y},0\n' for y in (.01, .015)),
])


class SignatureChecksTests(unittest.TestCase):
    def test_uses_cell_geometry_and_visibility(self):
        raw = (GRID + note('设计', .131, .139) + note('QA', .142, .152)
               + note('2026-11-09', .157, .168, layer='hidden')
               + note('', .162, .162, link='$PRPSHEET:"绘图日期"')
               + note('材料', .173, .179))
        cells = inspect_signature_cells(raw, (.13, .01, .18, .03))
        self.assertEqual([(c['field'], c['result']) for c in cells],
                         [('签名', 'filled'), ('日期', 'no_visible_note_text')])
        self.assertEqual(cells[1]['linkedProperties'], ['$PRPSHEET:"绘图日期"'])

    def test_legacy_note_without_visibility_is_unresolved(self):
        raw = GRID + note('设计', .131, .139) + note('QA', .142, .152, visibility=None)
        cells = inspect_signature_cells(raw, (.13, .01, .18, .03))
        self.assertEqual(cells[0]['result'], 'unresolved')
