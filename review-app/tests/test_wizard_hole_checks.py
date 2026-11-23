import unittest

from wizard_hole_checks import read_wizard_holes, wizard_group_evidence


VIEW = (
    'ViewTransform: 1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0,\n'
    'VisibleEdges: 2 CircleEdges: 2 Components: 1\n'
    'VisibleFullCircle: 0,0,0,0,0,1,.0045,\n'
    'VisibleFullCircle: .02,0,0,0,0,1,.0045,\n'
    'ModelWizardHole: cb | Counterbore | type=14 | size=M8 | points=2 | end=1\n'
    'ModelWizardHoleParameters: cb | 0,0,.009,0,0,.015,0,0,0,0,.009,\n'
    'ModelWizardHolePoint: cb | 0,0,0, | 0,0,0,\n'
    'ModelWizardHolePoint: cb | .02,0,0, | .02,0,0,\n'
)
OPENINGS = [dict(featureId='cb',circle=(x,0,0,0,0,1,.0045)) for x in (0,.02)]


class WizardHoleChecksTests(unittest.TestCase):
    def test_compound_count_requires_both_parameters_and_visible_positions(self):
        tokens = ('2 x ', '<MOD-DIAM>', ' 9 完全贯穿', '<HOLE-SPOT>',
                  '<MOD-DIAM>', ' 15 ', '<HOLE-DEPTH>', ' 9/背面')
        record = '\n'.join([*(f'RenderedDimText: {token} position=0,0,0,' for token in tokens),
                            'DimensionCircularOpening: wall | unique-circle | 0,0,0,0,0,1,.0045,'])
        holes = list(read_wizard_holes(VIEW))
        result = wizard_group_evidence(record,VIEW,2,holes,OPENINGS)
        self.assertEqual(result['result'],'matched_wizard_group')
        self.assertEqual(result['visibleHoleCount'],2)
        self.assertTrue(result['depthMatchesModel'])
        self.assertIsNone(wizard_group_evidence(record,VIEW.replace('VisibleFullCircle: .02','HiddenCircle: .02'),
                                               2,holes,OPENINGS))
        self.assertIsNone(wizard_group_evidence(record.replace(' 15 ',' 16 '),VIEW,2,holes,OPENINGS))
        walls = [
            dict(featureId='cb',wallId=f'{index}-{radius}',bodyId='body',
                 cylinder=(x,0,0,0,0,1,radius),
                 rims=[(x,0,z,0,0,1,radius) for z in (0,-.009)])
            for index,x in enumerate((0,.02)) for radius in (.0045,.0075)
        ]
        identified = wizard_group_evidence(record,VIEW,2,holes,OPENINGS,walls)['visibleHoles']
        self.assertEqual([hole['wallId'] for hole in identified], ['0-0.0045','1-0.0045'])
        self.assertEqual([stage['diameterMm'] for stage in identified[0]['boreStages']], [9.0,15.0])
        self.assertEqual([stage['cylindricalLengthMm'] for stage in identified[0]['boreStages']], [9.0,9.0])
