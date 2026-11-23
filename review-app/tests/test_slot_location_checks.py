import unittest

from slot_location_checks import _edge_path, _location_links
from test_dimension_checks import dimension


IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)
EDGE = (.01, .02, 0, .01, .06, 0)


def location_record(reference_x=.03):
    return '\n'.join([
        'View: Front',
        dimension('location', .01, .03).replace('Dangling: False', 'Dangling: False | AttachedCount: 2 |'),
        'RenderedDimText: 20 position=0.02,0.09,0,',
        'DimensionReferencePoint: .01,.04,0,',
        f'DimensionReferencePoint: {reference_x},.04,0,',
        'IndexedDimensionStraightEdge: 0 | .01,.02,0,.01,.06,0,',
        'AttachedSketchLine: 1 | .03,.035,0,.03,.045,0,',
        'AttachedSketchTransform: 1 | ' + ','.join(map(str, IDENTITY)),
    ])


class SlotLocationTests(unittest.TestCase):
    def test_requires_matching_witness_and_unique_slot_center(self):
        centers = {'A': (.03, .04)}
        links = list(_location_links(location_record(), IDENTITY, centers, [EDGE], []))
        self.assertEqual(len(links), 1)
        axis, nodes, evidence = links[0]
        self.assertEqual((axis, nodes[1], evidence['valueMm']), (0, ('slot', 'A'), 20))
        self.assertEqual(_edge_path({nodes[1]: [(nodes[0], evidence)]}, nodes[1]),
                         (nodes[0][1], [evidence]))
        self.assertFalse(list(_location_links(location_record(.031), IDENTITY, centers, [EDGE], [])))
        self.assertFalse(list(_location_links(location_record(), IDENTITY, centers, [EDGE], [EDGE])))
        centers['B'] = (.03, .044)
        self.assertFalse(list(_location_links(location_record(), IDENTITY, centers, [EDGE], [])))

    def test_spacing_without_attached_part_edge_is_not_anchored(self):
        first, second = ('slot', 'A'), ('slot', 'B')
        self.assertEqual(_edge_path({first: [(second, 'spacing')], second: [(first, 'spacing')]}, first),
                         (None, []))
