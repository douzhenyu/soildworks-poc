import json
import unittest

import server
from slot_checks import inspect_slots, slot_size_pairs


class SlotGeometryTests(unittest.TestCase):
    def pages(self, item):
        folder = server.DATA / '0d0ca1e2120746fb91cfd5a01fe408c7' / 'api-v4'
        path = folder / f'{item}.json'
        if not path.exists():
            self.skipTest('SolidWorks slot geometry batch is unavailable')
        return json.loads(path.read_text(encoding='utf-8-sig'))

    def test_opposite_openings_are_one_slot_and_dimensions_match_both_edges(self):
        pages = self.pages(2)
        self.assertEqual(pages[0]['raw'].count('ModelStraightSlotOpening: '), 8)
        slots, bindings = inspect_slots(pages)
        self.assertEqual(slots, 4)
        self.assertEqual(len(bindings), 2)
        pair, = slot_size_pairs(bindings)
        self.assertEqual(pair['scope'], 'matched_visible_set')
        self.assertEqual(pair['width']['valueMm'], 3.5)
        self.assertEqual(pair['length']['valueMm'], 7.5)
        self.assertEqual(len(pair['width']['visibleSlotIds']), 4)
        pages[0]['raw'] = pages[0]['raw'].replace('DimensionStraightEdge:', 'UnboundEdge:')
        self.assertFalse(slot_size_pairs(inspect_slots(pages)[1]))

    def test_detail_view_count_uses_its_parent_when_local_view_is_cropped(self):
        pages = self.pages(0)
        pairs = slot_size_pairs(inspect_slots(pages)[1])
        pair, = [pair for pair in pairs if pair['width']['quantity'] == 6]
        self.assertEqual(pair['scope'], 'matched_base_visible_set')
        self.assertEqual(len(pair['width']['visibleSlotIds']), 1)
        self.assertEqual(len(pair['width']['baseVisibleSlotIds']), 6)
        pages[0]['raw'] = pages[0]['raw'].replace('DetailBaseView:', 'UnknownBaseView:')
        pair, = [pair for pair in slot_size_pairs(inspect_slots(pages)[1]) if pair['width']['quantity'] == 6]
        self.assertEqual(pair['scope'], 'anchor_slot_only')
