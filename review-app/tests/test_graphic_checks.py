import unittest

from graphic_checks import graphic_inventory_read, page_graphics, watermark_evidence


def note(text, x, y, *, visibility=1):
    return (f'Note: {text}\nNoteExtent: {x},{y},0,{x+.003},{y+.002},0, Linked: {text}\n'
            f'NoteMetadata: visibility={visibility} | layer=visible | owner=1 | name=Test\n')


class GraphicChecksTests(unittest.TestCase):
    def test_region_and_visibility_are_required_for_text_stamp(self):
        raw = (note('大族数控 受控文件', .02, .19)
               + note('大族数控受控文件', .02, .02)
               + note('大族数控受控文件', .20, .19)
               + note('大族数控受控文件', .03, .18, visibility=3)
               + note('说明中提到大族数控受控文件', .04, .18))
        self.assertEqual([item['result'] for item in watermark_evidence(raw, .297, .21, [])],
                         ['controlled_stamp'])

    def test_picture_and_ole_geometry_only_flag_graphics_that_may_overlap(self):
        raw = ('GraphicInventory: template | pictures=2 | blocks=1\n'
               'View: Sheet Model: \nGraphicInventory: Sheet | pictures=0 | blocks=0\n'
               'GraphicPicture: template | 0 | .020,.175,.040,.185,\n'
               'GraphicPicture: template | 1 | .145,.015,.156,.02,\n'
               'GraphicBlock: template | 0 | .01,.18,0, | Block\n'
               'GraphicBlockText: template | 0 | Text\n'
               'GraphicOleCount: 1\nGraphicOle: 0 | .02,.175,.04,.185, | class\n')
        objects = page_graphics(raw)
        self.assertTrue(graphic_inventory_read(raw))
        self.assertFalse(graphic_inventory_read(raw + 'GraphicReadError: OLE\n'))
        marks = list(watermark_evidence(raw, .297, .21, objects))
        self.assertEqual([mark['kind'] for mark in marks], ['picture', 'block', 'ole'])
        self.assertTrue(all(mark['result'] == 'unverified_graphic' for mark in marks))
        self.assertEqual(marks[1]['definitionTexts'], ['Text'])
