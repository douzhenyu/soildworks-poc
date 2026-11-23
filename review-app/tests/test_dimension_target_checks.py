import unittest

from dimension_target_checks import dimension_target_evidence
from test_dimension_checks import dimension


VIEW = ('View: Front Model: Part\n'
        'ViewTransform: 1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0,\n'
        'PartViewInventory: True\nVisibleEdges: 1 CircleEdges: 1 Components: 1\n')


def diameter(tip, *, dangling='False', count=0):
    return (f'DIM: D | SystemValue: 0.02 | Type: 6 | Reference: True\n'
            f'DimPosition: .02,.03,0, | Dangling: {dangling} | AttachedCount: {count} | AttachedTypes: null |\n'
            f'DimensionArrowHead: 0,{tip},0,0,0,0,0,0,0,0,0,0,\n')


class DimensionTargetChecksTests(unittest.TestCase):
    def test_matches_tip_on_projected_circle_without_claiming_association(self):
        raw = VIEW + 'VisibleFullCircle: 0,0,0,0,.6,.8,.01,\n'
        self.assertEqual(list(dimension_target_evidence(raw+diameter(.008)))[0]['result'], 'visible_round_target')
        self.assertEqual(list(dimension_target_evidence(raw+diameter(.005)))[0]['result'], 'unresolved')
        self.assertEqual(list(dimension_target_evidence(raw+diameter(.008, dangling='True')))[0]['result'], 'dangling')
        self.assertEqual(list(dimension_target_evidence(raw+diameter(.008, count=1)
                                                      +'DimensionAttachmentNull: 0\n'))[0]['result'], 'unresolved')

    def test_duplicate_requires_same_cad_entities_and_displayed_text(self):
        def linear(name, second_entity):
            return (dimension(name, .01, .03, reference_y=.04)
                    .replace('AttachedCount: 2 |', 'AttachedCount: 2 | AttachedTypes: 1,1, |')
                    + '\nIndexedDimensionModelEdge: 0 | 1\nIndexedDimensionModelEdge: 1 | 1\n'
                    + f'AttachmentPersistentReference: 0 | model | A\nAttachmentPersistentReference: 1 | model | {second_entity}\n'
                    + 'RenderedDimText: 20 position=.02,.09,0,\n')
        matches = list(dimension_target_evidence(VIEW+linear('A','B')+linear('B','B')+linear('C','C')))
        self.assertEqual([row['result'] for row in matches], ['cad_geometry']*3+['coincident_dimensions'])
        self.assertEqual(matches[-1]['objectIds'], ['A','B'])

    def test_thread_symbol_requires_matching_center_and_axis_or_direct_circle(self):
        raw = (diameter(.008,count=1).replace('AttachedTypes: null','AttachedTypes: 39,')
               + 'DimensionThreadSymbol: 0 | ref | Thread\n'
               + 'DimensionThreadDisplayArc: 0 | 0,0,0,0,.01,0,0,0,.01,0,0,0,0,0,0,1,1,\n')
        supported = list(dimension_target_evidence(VIEW+'VisibleFullCircle: 0,0,0,0,0,1,.008,\n'+raw))[0]
        self.assertEqual(supported['result'],'thread_geometry')
        self.assertEqual(supported['threadTargets'][0]['targets'][0]['method'],'concentric_visible_edge')
        for other in ('0,0,0,0,1,0,.008,','.001,0,0,0,0,1,.008,'):
            self.assertEqual(list(dimension_target_evidence(VIEW+f'VisibleFullCircle: {other}\n'+raw))[0]['result'],
                             'unresolved')

    def test_null_linear_target_requires_witness_to_start_on_visible_edge(self):
        record = (dimension('A',.01,.03,reference_y=.04)
                  .replace('AttachedCount: 2 |','AttachedCount: 2 | AttachedTypes: 0,1, |')
                  + '\nDimensionAttachmentNull: 0\nIndexedDimensionModelEdge: 1 | 1\n')
        supported = VIEW+'VisibleStraightEdge: .01,.02,0,.01,.06,0,\n'+record
        self.assertEqual(list(dimension_target_evidence(supported))[0]['result'],'visible_witness_target')
        self.assertEqual(list(dimension_target_evidence(supported.replace('.01,.02,0,.01,.06,0',
                                                                         '.01,.05,0,.01,.06,0')))[0]['result'],
                         'unresolved')
