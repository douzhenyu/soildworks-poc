import unittest

from template_checks import COMPANY, OLD_COMPANY, TEMPLATE, inspect_sheet_format


BLOCK = (.132, .005, .292, .04)
INVENTORY = 'SheetFormatVisible: True\nTemplatePictures: 0 Blocks: 0\n'


def note(text, x, y, owner=2):
    return (f'Note: {text}\nNoteExtent: {x},{y},0,{x+.003},{y+.002},0, Linked: {text}\n'
            f'NoteMetadata: visibility=1 | layer=visible | owner={owner} | name=Test\n')


def results(raw):
    return {row['field']: row['result'] for row in inspect_sheet_format(raw, BLOCK)}


class TemplateChecksTests(unittest.TestCase):
    def test_requires_exact_format_fields_in_the_correct_regions(self):
        raw = (INVENTORY + note(COMPANY, .245, .006) + note(f'编号：{TEMPLATE}', .245, .002)
               + note('工艺', .16, .018, owner=1) + note(OLD_COMPANY, .045, .006))
        self.assertEqual(results(raw), dict(company='passed', template_code='passed', process_field='passed'))

    def test_old_format_evidence_takes_precedence_over_body_text(self):
        raw = (INVENTORY + note(OLD_COMPANY, .245, .006) + note(TEMPLATE[:-1], .245, .002)
               + note('工 艺：', .16, .018) + note(TEMPLATE, .245, .002, owner=1))
        self.assertEqual(results(raw), dict(company='failed', template_code='failed', process_field='failed'))

    def test_graphical_template_objects_leave_absence_of_process_label_pending(self):
        raw = INVENTORY.replace('Blocks: 0', 'Blocks: 1') + note(COMPANY, .245, .006)
        self.assertEqual(results(raw), dict(company='passed', template_code='pending', process_field='pending'))
