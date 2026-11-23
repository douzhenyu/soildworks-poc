import unittest

from title_field_checks import inspect_title_fields


BLOCK = (.132, .005, .292, .04)
GRID = ''.join([
    *(f'FrameLine: {x},.005,0 -> {x},.04,0\n' for x in (.132,.226,.242,.292)),
    *(f'FrameLine: .132,{y},0 -> .292,{y},0\n' for y in (.005,.01,.015,.02,.04)),
    *(f'FrameLine: .242,{y},0 -> .292,{y},0\n' for y in (.02178,.0284,.035)),
])


def note(text, x, y, link='', owner=2):
    return (f'Note: {text}\nNoteExtent: {x},{y},0,{x+.002},{y+.001},0, Linked: {link}\n'
            f'NoteMetadata: visibility=1 | layer=visible | owner={owner} | name=Test\n')


def inspect(raw, graphics=()):
    return {row['field']: row for row in inspect_title_fields(GRID+raw, BLOCK, True, graphics, True)}


class TitleFieldChecksTests(unittest.TestCase):
    def test_uses_only_value_in_the_correct_cell(self):
        raw = (note('ABC-123', .263,.024, '$PRPSHEET:"代号"')
               + note('', .263,.017, '$PRPSHEET:"物料编码"')
               + note('SUS304', .232,.017, '$PRPSHEET:"材料"')
               + note('HANS', .261,.037, '$PRPSHEET:"机型"')
               + note('手填零件名称', .251,.031, owner=1))
        checks = inspect(raw)
        self.assertEqual(checks['图号']['result'], 'present_linked')
        self.assertEqual(checks['零件名称']['result'], 'present_manual')
        self.assertEqual(checks['机型']['values'][0]['text'], 'HANS')
        self.assertFalse(checks['数量']['required'])

    def test_a_deleted_or_unresolved_note_cannot_be_replaced_by_model_metadata(self):
        raw = ('TitleProperty: model\t材料\t2\tTrue\tFalse\tSUS304\tSUS304\n'
               + note('SUS304', .08,.15, '$PRPSHEET:"材料"', owner=0)
               + note('', .263,.024, '$PRPSHEET:"代号"'))
        checks = inspect(raw)
        self.assertEqual(checks['材料']['result'], 'empty')
        self.assertEqual(checks['材料']['sourceProperties'][0]['evaluated'], 'SUS304')
        self.assertEqual(checks['图号']['result'], 'blank_link')
        self.assertEqual(inspect(raw, [dict(bounds=(.23,.016,.24,.019))])['材料']['result'], 'pending')
