import unittest

from hole_checks import repeated_hole_counts


def fixture():
    return '\r\n'.join([
        'DrawingUnits: 0,1,0,2,0,',
        'View: Front Model: part.SLDPRT',
        'ViewMetadata: type=7 | scale=1 | configuration=Default',
        'PartViewInventory: True',
        'ModelInwardCylinder: 0,0,0,0,0,1,0.0045,',
        'ModelInwardCylinder: 0.02,0,0,0,0,1,0.0045,',
        'ModelCylinderInventoryEnd',
        'DIM: D1 | SystemValue: 0.009 | Type: 6 | Reference: True',
        'DimTextParts:  | 3-<MOD-DIAM> |  | ',
        'RenderedDimText: 3- position=0.05,0.10,0,',
        'RenderedDimText: <MOD-DIAM> position=0.05,0.10,0,',
        'RenderedDimText: 9 position=0.05,0.10,0,',
        'DimPosition: 0.05,0.10,0, | Dangling: False',
        'DimensionCircularOpening: wall1 | unique-circle | 0,0,0,0,0,1,0.0045,',
    ])


class HoleCountTests(unittest.TestCase):
    def test_quantity_uses_one_model_inventory_and_requires_binding(self):
        raw = fixture()
        claims = list(repeated_hole_counts(raw))
        self.assertEqual(len(claims), 1)
        self.assertEqual((claims[0]['quantity'], claims[0]['diameterMm'], claims[0]['modelUpperBound']), (3, 9, 2))
        self.assertEqual(claims[0]['result'], 'exceeds_bound')
        for modified in (raw.replace('ModelCylinderInventoryEnd', ''),
                         raw.replace('DimensionCircularOpening:', 'Unbound:'),
                         raw.replace('3-<MOD-DIAM>', '3-M9')):
            self.assertEqual(list(repeated_hole_counts(modified))[0]['result'], 'unverified_round_claim')
        self.assertFalse(list(repeated_hole_counts(raw.replace('PartViewInventory: True', 'PartViewInventory: False'))))

    def test_group_requires_same_feature_and_distinct_visible_holes(self):
        geometry = '\r\n'.join([
            'ViewTransform: 1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0,',
            'VisibleEdges: 4 CircleEdges: 4 Components: 1',
            'VisibleFullCircle: 0,0,0,0,0,1,0.0045,',
            'VisibleFullCircle: 0.02,0,0,0,0,1,0.0045,',
            'VisibleFullCircle: 0.04,0,0,0,0,1,0.0045,',
            'ModelCircularOpening: e1 | wall1 | 0,0,0,0,0,1,0.0045, | Hole A | owner=feature1 | type=HoleWzd',
            'ModelCircularOpening: e2 | wall2 | 0.02,0,0,0,0,1,0.0045, | Hole A | owner=feature1 | type=HoleWzd',
            'ModelCircularOpening: e3 | wall3 | 0.02,0,-0.01,0,0,1,0.0045, | Hole A | owner=feature1 | type=HoleWzd',
            'ModelCircularOpening: e4 | wall4 | 0.04,0,0,0,0,1,0.0045, | Hole B | owner=feature2 | type=HoleWzd',
            'ModelCircularOpeningInventoryEnd',
        ])
        raw = fixture().replace('ModelCylinderInventoryEnd', geometry+'\r\nModelCylinderInventoryEnd').replace('3-', '2-')
        claim = list(repeated_hole_counts(raw))[0]
        self.assertEqual(claim['result'], 'matched_visible_feature_group')
        self.assertEqual((claim['visibleHoleCount'], claim['modelFeatureWallCount']), (2,3))
        self.assertEqual({hole['wallId'] for hole in claim['visibleHoles']}, {'wall1','wall2'})
        overlapped = raw.replace('VisibleEdges: 4', 'VisibleFullCircle: 0.02,0,-0.01,0,0,1,0.0045,\r\nVisibleEdges: 4')
        self.assertEqual(list(repeated_hole_counts(overlapped))[0]['result'], 'within_bound')
        pattern = raw.replace('2-', '3-').replace(
            'ModelCylinderInventoryEnd',
            'ModelInwardCylinder: 0.04,0,0,0,0,1,0.0045,\r\nModelPatternSeed: feature2 | feature1 | LPattern\r\nModelCylinderInventoryEnd')
        self.assertEqual(list(repeated_hole_counts(pattern))[0]['result'], 'within_bound')
        distinct = pattern.replace('e3 | wall3 | 0.02,0,-0.01', 'e3 | wall2 | 0.02,0,-0.01')
        claim = list(repeated_hole_counts(distinct))[0]
        self.assertEqual(claim['result'], 'matched_pattern_group')
        self.assertEqual({hole['wallId'] for hole in claim['patternHoles']}, {'wall1','wall2','wall4'})
